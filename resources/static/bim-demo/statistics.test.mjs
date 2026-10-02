import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {modelStatistics} from './statistics.js';
const recording=JSON.parse(readFileSync(new URL('./demo-recording.json',import.meta.url)));

test('house switches update every indicator and report from the visible model',()=>{
  for(let i=0;i<4;i++) {
    const stats=modelStatistics(recording,i+2),house=recording.houses[i];
    assert.equal(stats.name,house.name);
    assert.equal(stats.report,house.report);
    for(const metric of stats.metrics) {
      const b=house.provisional.building;
      assert.equal(metric.value,b[metric.key]??b[metric.key+'_known_subtotal']??null);
      assert.equal(metric.subtotal,b[metric.key]==null&&b[metric.key+'_known_subtotal']!=null);
    }
    assert.equal(stats.complete,false);
    assert.equal(stats.schemaDiagnostics,house.diagnostic_count);
  }
  assert.notEqual(modelStatistics(recording,2).metrics[3].value,modelStatistics(recording,3).metrics[3].value);
});
test('returning to fixtures restores fixture statistics and report',()=>{
  modelStatistics(recording,5);
  const stats=modelStatistics(recording,0);
  assert.equal(stats.name,'Example A');
  assert.equal(stats.metrics[3].value,recording.models[0].building.gwp_a1_a3_b4);
  assert.equal(stats.metrics[3].subtotal,false);
  assert.equal(stats.report,recording.models[0].report);
});
test('missing indicators stay unavailable; negative known subtotals are retained',()=>{
  assert.equal(modelStatistics(recording,2).metrics.at(-1).value,null);
  assert.ok(modelStatistics(recording,3).metrics[3].value<0);
});

test('recovery cost analysis follows every house and fixture without mixing datasets',()=>{
 for(let active=0;active<6;active++) {
  const stats=modelStatistics(recording,active);
  const entry=active<2?recording.models[active]:recording.houses[active-2].provisional;
  assert.equal(stats.recoveryCost,entry.recovery_cost_analysis);
  assert.equal(stats.recoveryCost.currency,'EUR');
  const subtotal=stats.recoveryCost.groups.reduce((sum,group)=>sum+group.known_cost_eur,0);
  assert.ok(Math.abs(subtotal-entry.building.global_brutto_price_known_subtotal)<.01);
 }
});
