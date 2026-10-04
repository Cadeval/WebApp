const INDICATORS = [
  ['Installed mass','mass','kg'],
  ['Cumulative consumption','mass_observation','kg'],
  ['GWP A1–A3','gwp_a1_a3','kg CO₂e'],
  ['GWP A1–A3 + B4','gwp_a1_a3_b4','kg CO₂e'],
  ['AP A1–A3','ap_a1_a3','kg SO₂e'],
  ['AP A1–A3 + B4','ap_a1_a3_b4','kg SO₂e'],
  ['PENRT A1–A3','penrt_a1_a3','MJ'],
  ['PENRT A1–A3 + B4','penrt_a1_a3_b4','MJ'],
  ['Volume','volume','m³'],
  ['Initial gross material cost','global_brutto_price','€'],
  ['Recovery grade','recycling_grade',''],
];

// Resolve everything from the same scene index that selects visible geometry.
export function modelStatistics(recording, active) {
  const house=active>=recording.models.length;
  const entry=house?recording.houses[active-recording.models.length]:recording.models[active];
  const estimate=house?entry.provisional:entry;
  const building=estimate?.building??{};
  return {
    name:entry.name, report:entry.report, recoveryCost:estimate?.recovery_cost_analysis??null, complete:estimate?.complete??false,
    schemaDiagnostics:entry.diagnostic_count??0, issueCount:estimate?.issue_count??0,
    metrics:INDICATORS.map(([label,key,unit])=>({
      label,key,unit,value:building[key]??building[key+'_known_subtotal']??null,
      subtotal:building[key]==null && building[key+'_known_subtotal']!=null,
    })),
  };
}
