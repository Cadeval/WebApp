import test from 'node:test';
import assert from 'node:assert/strict';
import {DURATION, STEPS, stepIndex, tick, restart, toggle} from './playback.js';

test('the recorded timeline visits each step in order and finishes',()=>{
  let state=toggle(restart());
  for(let step=0;step<STEPS.length;step++) {
    assert.equal(stepIndex(state.seconds),step);
    state=tick(state,12);
  }
  assert.deepEqual(state,{seconds:DURATION,playing:false});
});
test('pause holds both time and camera position; resume continues',()=>{
  let state=tick(toggle(restart()),19);
  state=toggle(state);
  assert.deepEqual(tick(state,100),state);
  assert.deepEqual(tick(toggle(state),5),{seconds:24,playing:true});
});
test('restart resets the sequence and remains paused',()=>{
  assert.deepEqual(restart(),{seconds:0,playing:false});
  assert.equal(stepIndex(restart().seconds),0);
});
test('replay at the endpoint begins from the first recorded scene',()=>{
  assert.deepEqual(toggle({seconds:72,playing:false}),{seconds:0,playing:true});
});
test('boundary steps and background-tab time remain deterministic',()=>{
  assert.equal(stepIndex(11.999),0);
  assert.equal(stepIndex(12),1);
  assert.equal(stepIndex(72),5);
  assert.deepEqual(tick({seconds:60,playing:true},120),{seconds:72,playing:false});
  assert.deepEqual(tick({seconds:6,playing:true},-10),{seconds:6,playing:true});
});

import {sceneIndex} from './playback.js';
test('actual houses A–D appear in order before and after the controlled calculations',()=>{
  assert.deepEqual([0,3,6,9,12,36,48,60,63,66,69,72].map(sceneIndex),[2,3,4,5,0,0,1,2,3,4,5,5]);
});

import {seekStep,cycleHouse} from './playback.js';
test('manual steps pause and clamp at timeline boundaries',()=>{
 assert.deepEqual(seekStep({seconds:19,playing:true},1),{seconds:24,playing:false});
 assert.deepEqual(seekStep({seconds:19,playing:true},-1),{seconds:0,playing:false});
 assert.equal(seekStep({seconds:0,playing:false},-1).seconds,0);
 assert.equal(seekStep({seconds:72,playing:false},1).seconds,60);
});
test('manual house cycle wraps D to A and starts on A from fixtures',()=>{
 assert.deepEqual([-1,0,1,2,3].map(i=>cycleHouse(i)),[0,1,2,3,0]);
});
