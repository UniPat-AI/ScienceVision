Domain: physics video / simulation VQA (Genesis rollouts — rigid bodies,
granular media, deformables/cloth, contact and friction; GLB animations of a
pre-computed rollout that the agent observes but may NOT re-simulate).

Grade with these domain priorities:
- Physical-outcome correctness: the predicted outcome must match what the
  rollout actually does — does the grasp hold, does the object lift, does the
  material fracture/drape/cascade as observed. The structured ground truth
  (e.g. grasp_holds, cube_lifted, net_rise_class) is authoritative; the
  exact_match scorer carries the bulk of this signal, so the judge should
  reward answers that are physically consistent even when phrased differently.
- Mechanism over guess: credit answers that reason from the right mechanism
  (friction-limited contact vs. weight, force vs. position control, granular
  angle of repose, cloth self-collision) rather than a lucky label. Penalize
  answers that merely restate the prompt without committing to an outcome.
- No-resimulation fairness: these tasks forbid stepping the physics engine /
  querying ground-truth state (scene.step, get_pos, get_contacts, …). An answer
  that is only justifiable by having re-run the simulator, or that quotes exact
  internal quantities it could not have observed, should not get full credit —
  the task tests contact-mechanics intuition from observation.
- Quantitative bins: when the answer is a class/bin (e.g. net_rise_class
  "~15-20cm"), the correct bin gets full credit; an adjacent bin gets partial;
  a wrong sign or order-of-magnitude gets little.

Partial credit for the right qualitative outcome with a wrong magnitude/bin, or
for correct mechanism reasoning with one structured field wrong.
