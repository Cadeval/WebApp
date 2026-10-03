import * as THREE from 'three';
import {modelStatistics} from './statistics.js';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {DURATION, STEPS, stepIndex, tick, restart, toggle, sceneIndex, seekStep, cycleHouse} from './playback.js';
import {createGeometryActivity, loadGeometry} from '../js/geometry_loading.js?v=0.8.0';
import {createLandscape} from '../js/viewer_landscape.js?v=0.8.0';
import {createRenderResources, createOutdoorLights, disposeModelResources, fitShadowToBounds} from '../js/viewer_rendering.js?v=0.8.0';

const formatted = value => value == null ? 'Unavailable' : new Intl.NumberFormat(undefined,{maximumFractionDigits:3}).format(value);
const clock = value => `${Math.floor(value/60)}:${String(Math.floor(value%60)).padStart(2,'0')}`;
export function renderPlaybackPosition(root, seconds) {
  root.querySelector('[data-demo-playback-progress]').value=seconds;
  root.querySelector('.demo-clock').textContent=`${clock(seconds)} / 1:12`;
}
function text(tag, value) {const node=document.createElement(tag); node.textContent=value; return node;}
function definition(rows) {const list=document.createElement('dl'); for(const [label,value] of rows) list.append(text('dt',label),text('dd',String(value))); return list;}
function table(headers,rows) {const result=document.createElement('table');const head=document.createElement('thead');const hrow=document.createElement('tr');for(const h of headers) hrow.append(text('th',h));head.append(hrow);result.append(head);const body=document.createElement('tbody');for(const values of rows){const row=document.createElement('tr');for(const value of values)row.append(text('td',String(value)));body.append(row);}result.append(body);return result;}

export function recenterPreviewModel(model) {
  const bounds=new THREE.Box3().setFromObject(model);
  if(!bounds.isEmpty())model.position.sub(bounds.getCenter(new THREE.Vector3()));
  model.updateMatrixWorld(true);
  return new THREE.Box3().setFromObject(model);
}

export function fitPreviewGrid(grid,bounds) {
  const size=bounds.getSize(new THREE.Vector3()),center=bounds.getCenter(new THREE.Vector3());
  const extent=Math.max(size.x,size.z,1)*1.35;
  grid.scale.set(extent/12,1,extent/12);
  grid.position.set(center.x,bounds.min.y-Math.max(.01,Math.max(size.x,size.y,size.z)*.002),center.z);
}

const recordings=new WeakSet();
export async function initializeRecording(root) {
  if(!root || recordings.has(root)) return;
  recordings.add(root);
  const pick=selector=>root.querySelector(selector);
  const play=pick('[data-demo-play]'),reset=pick('[data-demo-restart]');
  const status=pick('[data-demo-state]');
  const viewport=pick('.demo-scene'),abortLoad=new AbortController(),listeners=[];
  let loading=createGeometryActivity({target:viewport,overlay:pick('[data-demo-loading]'),message:pick('[data-demo-loading-message]'),detail:pick('[data-demo-loading-detail]'),progress:pick('[data-demo-loading-progress]'),label:'demo recording'});
  loading.stage('Loading demo recording…');
  const previousStep=pick('[data-demo-previous]'),nextStep=pick('[data-demo-next]'),houseButton=pick('[data-demo-house]');
  let state=restart(),disposed=false,renderer,environment,outdoorLights,landscape,sceneBounds,grid,observer,themeObserver,frame=null,renderUpdate,renderCount=0,previous,lastRendered,shown=-1,loadedModels=[],launch,manualHouse=null,shownHouse=-1;
  function listen(target,event,callback){target.addEventListener(event,callback);listeners.push([target,event,callback]);}
  function requestRender(){if(!disposed && frame===null)frame=requestAnimationFrame(now=>{
    frame=null;
    try{renderUpdate?.(now);}catch(error){
      if(disposed)return;
      status.textContent='Preview unavailable';pick('[data-demo-detail]').replaceChildren(text('p',`The 3D preview could not be rendered: ${error.message}`));dispose();
    }
  });}
  function dispose(){
    if(disposed)return;
    disposed=true;abortLoad.abort();loading.finish();recordings.delete(root);
    play.removeEventListener('click',launch);cancelAnimationFrame(frame);frame=null;observer?.disconnect();themeObserver?.disconnect();
    for(const [target,event,callback] of listeners)target.removeEventListener(event,callback);
    landscape?.dispose();outdoorLights?.dispose();environment?.dispose();grid?.geometry.dispose();grid?.material.dispose();renderer?.dispose();
    for(const model of loadedModels)disposeModelResources(model);
    loadedModels=[];document.body.removeEventListener('htmx:before:cleanup',cleanup);
  }
  function cleanup(event){if(event.target===root || event.target?.contains(root))dispose();}
  document.body.addEventListener('htmx:before:cleanup',cleanup);
  try {
    const recordingUrl=new URL(root.dataset.recordingUrl,location.href);
    const response=await fetch(recordingUrl,{signal:abortLoad.signal,credentials:'same-origin'});
    if(!response.ok) throw new Error(`Recording unavailable (${response.status})`);
    const recording=await response.json();
    if(disposed) return;
    if(recording.kind!=='prerecorded-controlled-fixture-demo' || recording.models.length!==2) throw new Error('Unsupported recording.');
    pick('[data-demo-report]').href=new URL(recording.models[0].report,recordingUrl).href;
    // Large real models are loaded only after visitors deliberately start playback.
    status.textContent='Ready';play.disabled=false;play.textContent='Load and play demo';
    loading.finish();
    pick('[data-demo-title]').textContent='Actual house geometry and a recorded calculation workflow';
    pick('[data-demo-caption]').textContent='Play to load the A–D geometry previews. House values are provisional known subtotals with validation warnings; the walkthrough also includes verified controlled examples.';
    await new Promise(resolve=>{launch=()=>{play.removeEventListener('click',launch);abortLoad.signal.removeEventListener('abort',resolve);play.disabled=true;status.textContent='Loading saved geometry…';resolve();};play.addEventListener('click',launch);abortLoad.signal.addEventListener('abort',resolve,{once:true});});
    if(disposed)return;
    const entries=[...recording.models,...recording.houses];
    const models=[];
    // Sequential loads bound transient parsing memory on the unauthenticated home.
    for(const [index,entry] of entries.entries()){
      if(disposed)return;
      status.textContent=`Loading ${entry.name} · ${index+1} of ${entries.length}`;
      loading=createGeometryActivity({target:viewport,overlay:pick('[data-demo-loading]'),message:pick('[data-demo-loading-message]'),detail:pick('[data-demo-loading-detail]'),progress:pick('[data-demo-loading-progress]'),label:`${entry.name} (${index+1} of ${entries.length})`});
      const gltf=await loadGeometry(new URL(entry.asset,recordingUrl).href,{loader:new GLTFLoader(),signal:abortLoad.signal,onStage:loading.stage,onProgress:loading.transfer});
      if(disposed){disposeModelResources(gltf.scene);return;}
      recenterPreviewModel(gltf.scene);
      gltf.scene.traverse(part=>{if(!part.isMesh)return;const materials=Array.isArray(part.material)?part.material:[part.material];part.castShadow=materials.every(material=>material && !material.transparent && !material.transmission);part.receiveShadow=materials.some(material=>material && !material.transparent);});
      models.push(gltf.scene);loadedModels=models;
      if(index<entries.length-1)loading.finish();
    }
    const scene=new THREE.Scene();scene.background=new THREE.Color();
    ({renderer,environment}=createRenderResources(pick('canvas'),scene));
    outdoorLights=createOutdoorLights(scene);
    const camera=new THREE.PerspectiveCamera(38,1,.01,100);
    const bounds=models.map(model=>new THREE.Box3().setFromObject(model));
    const centers=bounds.map(box=>box.getCenter(new THREE.Vector3()));
    const sizes=bounds.map(box=>box.getSize(new THREE.Vector3()));
    for(const model of models)scene.add(model);
    grid=new THREE.GridHelper(12,12,0x999999,0xcccccc);
    grid.material.opacity=.28;grid.material.transparent=true;grid.material.depthWrite=false;scene.add(grid);
    observer=new ResizeObserver(()=>{const w=Math.max(viewport.clientWidth,1),h=Math.max(viewport.clientHeight,1);renderer.setSize(w,h,false);camera.aspect=w/h;camera.updateProjectionMatrix();requestRender();});observer.observe(viewport);
    const detail=pick('[data-demo-detail]');
    const motionPreference=matchMedia('(prefers-reduced-motion: reduce)');
    let reduceMotion=motionPreference.matches;
    listen(motionPreference,'change',event=>{reduceMotion=event.matches;requestRender();});
    listen(matchMedia('(prefers-color-scheme: dark)'),'change',requestRender);
    themeObserver=new MutationObserver(requestRender);
    themeObserver.observe(document.documentElement,{attributes:true,attributeFilter:['class','style','data-theme']});
    themeObserver.observe(document.body,{attributes:true,attributeFilter:['class','style','data-theme']});
    function showStep(index){
      const step=STEPS[index],a=recording.models[0].building,b=recording.models[1].building;
      pick('.demo-step').textContent=`Step ${index+1} of ${STEPS.length}`;
      pick('[data-demo-title]').textContent=step.title;pick('[data-demo-caption]').textContent=step.caption;
      let content;
      if(index===0)content=definition([['Actual geometry','Houses A, B, C and D'],['Source','Supplied 28V_new IFC exports'],['House assessments','Rejected invalid input'],['Calculation chapters','Separate controlled examples'],['Library writes','None']]);
      if(index===1)content=table(['Material','Density kg/m³','Service life'],recording.reference.display_rows.map(r=>[r.name,formatted(r.Dichte),`${formatted(r.Nutzungsdauer)} years`]));
      if(index===2)content=definition([['Observation span','50 years'],['Replacement endpoint','Included'],['Recovery weighting','Installed mass'],['LCA denominator','Initially installed kg'],['Excluded','Windows and doors']]);
      if(index===3)content=definition([['Installed mass',`${formatted(a.mass)} kg`],['Cumulative consumption',`${formatted(a.mass_observation)} kg`],['GWP A1–A3',`${formatted(a.gwp_a1_a3)} kg CO₂e`],['GWP A1–A3 + B4',`${formatted(a.gwp_a1_a3_b4)} kg CO₂e`],['Initial gross material cost',`${formatted(a.global_brutto_price)} €`],['Recovery grade',formatted(a.recycling_grade)]]);
      if(index===4){content=document.createElement('div');content.append(table(['Recorded indicator','Example A','Example B'],[['Installed mass, kg',formatted(a.mass),formatted(b.mass)],['GWP A1–A3 + B4, kg CO₂e',formatted(a.gwp_a1_a3_b4),formatted(b.gwp_a1_a3_b4)],['Initial gross material cost, €',formatted(a.global_brutto_price),formatted(b.global_brutto_price)]]));const bars=document.createElement('div');bars.className='demo-bars';for(const [label,value] of [['Example A',a.mass],['Example B',b.mass]]){const row=document.createElement('div');row.append(text('span',`${label} · ${formatted(value)} kg`));const bar=document.createElement('i');bar.style.setProperty('--bar',`${100*value/a.mass}%`);row.append(bar);bars.append(row);}content.append(bars);}
      if(index===5)content=table(['Actual house','Provisional estimate coverage'],recording.houses.map(h=>[h.name,`${formatted(h.diagnostic_count)} schema diagnostics · ${formatted(h.provisional?.issue_count)} calculation issues`]));
      detail.replaceChildren(content);
      pick('[data-demo-report]').href=new URL(recording.models[index===4?1:0].report,recordingUrl).href;
      pick('[data-demo-report]').textContent='Download recorded example report';
    }
    function showHouse(active){
      const panel=pick('[data-demo-house-values]');
      panel.replaceChildren();
      if(active<2)return;
      const stats=modelStatistics(recording,active);
      panel.append(text('strong',`${stats.name} · provisional, unvalidated`));
      const rows=stats.metrics.map(metric=>[metric.label,
        metric.value==null ? 'Unavailable' : `${formatted(metric.value)} ${metric.unit}${metric.subtotal?' · known subtotal; total unavailable':''}`]);
      panel.append(definition(rows.filter((_,index)=>[0,3,9].includes(index))));
      pick('[data-demo-title]').textContent=`${stats.name} · provisional statistics`;
      pick('[data-demo-caption]').textContent='Pre-calculated values for the house currently shown. Strict IFC validation failed. Known subtotals include only available contributions and are not complete house totals.';
      detail.replaceChildren(definition(rows));
      const warning=`${formatted(stats.schemaDiagnostics)} schema diagnostics · ${formatted(stats.issueCount)} calculation issues. ${stats.complete?'Calculation data complete':'Incomplete calculation data'}.`;
      panel.append(text('p',warning));detail.append(text('p',warning));
      const report=pick('[data-demo-report]');report.href=new URL(stats.report,recordingUrl).href;report.textContent=`Download ${stats.name} provisional values and coverage`;
    }

    function showRecovery(active){
      const stats=modelStatistics(recording,active),panel=pick('[data-demo-recovery]');
      panel.replaceChildren(text('strong',`${stats.name}${active>=2?' · provisional':''}`));
      const analysis=stats.recoveryCost;
      if(!analysis){panel.append(text('p','Recovery cost breakdown unavailable.'));return;}
      panel.append(table(['Recovery grade','Known cost, €','Share of known cost','Missing prices'],analysis.groups.map(group=>[
        group.grade==null?'Ungraded':String(group.grade),formatted(group.known_cost_eur),
        group.share_of_known_cost_percent==null?'Unavailable':`${formatted(group.share_of_known_cost_percent)}%`,
        `${group.missing_price_rows} / ${group.rows}`,
      ])));
      panel.append(text('p',`Known initial gross material cost: ${formatted(analysis.known_cost_eur)} €. Shares use this known subtotal, not a complete house total. Missing prices are excluded from the subtotal; ungraded costs remain separate.`));
      panel.append(text('p','Grades classify the purchase cost of materials. They do not specify resale revenue, recovery savings, dismantling charges or disposal fees.'));
    }
    function update(now){
      frame=null;
      if(disposed)return;
      // The recorded camera needs at most 30 frames/second. Paused and manual
      // house views render only when their content or size changes.
      if(state.playing && lastRendered!=null && now-lastRendered<1000/30){requestRender();return;}
      lastRendered=now;
      state=tick(state,previous==null?0:(now-previous)/1000);previous=now;
      const index=stepIndex(state.seconds);
      const active=manualHouse==null?sceneIndex(state.seconds):2+manualHouse;
      if(index!==shown || active!==shownHouse){showStep(index);showHouse(active);showRecovery(active);shown=index;}
      if(active!==shownHouse){
        if(landscape){scene.remove(landscape.group);landscape.dispose();}
        landscape=createLandscape(bounds[active]);scene.add(landscape.group);
        sceneBounds=bounds[active].clone().union(new THREE.Box3().setFromObject(landscape.group));
        fitShadowToBounds(outdoorLights.sun,bounds[active]);renderer.shadowMap.needsUpdate=true;shownHouse=active;
      }
      previousStep.disabled=index===0;nextStep.disabled=index===STEPS.length-1;
      for(let i=0;i<models.length;i++)models[i].visible=i===active;
      const center=centers[active],size=sizes[active];
      fitPreviewGrid(grid,bounds[active]);
      const angle=reduceMotion ? .75 : .75+state.seconds*.025;
      const distance=Math.max(size.x,size.y,size.z,1)*2.1/Math.min(Math.max(camera.aspect,.1),1);
      camera.position.set(center.x+Math.cos(angle)*distance,center.y+distance*.42,center.z+Math.sin(angle)*distance);
      camera.near=Math.max(.01,distance/100);camera.far=Math.max(10,distance*1.5+sceneBounds.getSize(new THREE.Vector3()).length());camera.updateProjectionMatrix();
      camera.lookAt(center);
      // Follow the shared landing-page palette, including theme changes.
      scene.background.set(getComputedStyle(viewport).backgroundColor);
      renderer.render(scene,camera);
      root.dataset.renderFrames=String(++renderCount);root.dataset.demoReady='true';
      loading.finish();
      pick('.demo-model-label').textContent=`${entries[active].name} · ${active<2?'controlled calculation fixture':'actual house · provisional estimates'}`;
      renderPlaybackPosition(root,state.seconds);
      play.textContent=state.playing?'Pause':state.seconds>=DURATION?'Replay':'Play walkthrough';
      play.setAttribute('aria-pressed',String(state.playing));
      const label=state.seconds>=DURATION?'Finished':state.playing?'Playing':'Paused';
      if(status.textContent!==label)status.textContent=label;
      if(state.playing)requestRender();
    }
    renderUpdate=update;
    play.disabled=reset.disabled=houseButton.disabled=previousStep.disabled=nextStep.disabled=false;
    const move=direction=>{state=seekStep(state,direction);manualHouse=null;previous=undefined;lastRendered=undefined;requestRender();};
    listen(previousStep,'click',()=>move(-1));listen(nextStep,'click',()=>move(1));
    listen(houseButton,'click',()=>{const active=manualHouse==null?sceneIndex(state.seconds)-2:manualHouse;manualHouse=cycleHouse(active<0?-1:active);state={...state,playing:false};previous=undefined;lastRendered=undefined;requestRender();});
    listen(play,'click',()=>{manualHouse=null;state=toggle(state);previous=undefined;lastRendered=undefined;requestRender();});
    listen(reset,'click',()=>{manualHouse=null;state=restart();previous=undefined;lastRendered=undefined;shown=-1;requestRender();});
    state=toggle(restart());
    loading.stage('rendering');requestRender();
  } catch(error) {
    if(disposed || error.name==='AbortError'){dispose();return;}
    status.textContent='Recording unavailable';
    const message=text('p',`The saved demonstration could not be loaded: ${error.message}`);message.className='demo-error';pick('[data-demo-detail]').replaceChildren(message);
    dispose();
  }
}
if(typeof document!=='undefined') {
  initializeRecording(document.querySelector('[data-recorded-demo]'));
  document.body.addEventListener('htmx:after:settle',()=>initializeRecording(document.querySelector('[data-recorded-demo]')));
}
