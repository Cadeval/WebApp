export const DURATION = 72;
export const STEPS = [
  {start:0, title:'Inspect the actual A–D models', caption:'These are saved geometry previews of the four supplied house models. Rendering is independent of assessment validation. The calculation chapters use clearly labelled controlled fixtures.'},
  {start:12, title:'Select an external reference', caption:'The recorded example uses the authoritative June 2026 workbook. These two material rows supply density, impacts, service life and prices; the source workbook contains 115 records.'},
  {start:24, title:'Make the method explicit', caption:'This recorded scenario uses 50 years, includes replacement at the endpoint, weights recovery grades by installed mass and normalises both impact periods by initially installed mass.'},
  {start:36, title:'Read the saved material passport', caption:'The recorded report keeps installed mass, cumulative replacement consumption, environmental impacts and initial material costs separate. Missing data stays unavailable.'},
  {start:48, title:'Compare consistent boundaries', caption:'Example B is a shorter version of the same controlled wall. Both recordings use the same reference, policies and exclusions. These results demonstrate a workflow, not an architectural quality ranking.'},
  {start:60, title:'Review the evidence and limits', caption:'Playback shows saved geometry and verified fixture results. Rendering is separate from schema validation. All four supplied A–D exports fail the assessment gate, provisional house estimates are shown with validation and coverage warnings.'},
];
export function stepIndex(seconds) {
  let index = 0;
  for (let i=0; i<STEPS.length; i++) if (seconds >= STEPS[i].start) index=i;
  return index;
}
export function tick(state, delta) {
  if (!state.playing) return {...state};
  const seconds = Math.min(DURATION, state.seconds + Math.max(0, delta));
  return {seconds, playing:seconds < DURATION};
}
export function restart() { return {seconds:0, playing:false}; }
export function toggle(state) {
  return state.seconds >= DURATION ? {seconds:0, playing:true} : {...state, playing:!state.playing};
}

export function sceneIndex(seconds) {
  const index = stepIndex(seconds);
  return (index === 0 || index === 5)
    ? 2 + Math.max(0, Math.min(3, Math.floor((seconds - STEPS[index].start) / 3)))
    : (index === 4 ? 1 : 0);
}

export function seekStep(state, direction) {
  const index=Math.max(0,Math.min(STEPS.length-1,stepIndex(state.seconds)+direction));
  return {seconds:STEPS[index].start,playing:false};
}
export function cycleHouse(current, count=4) { return (current+1)%count; }
