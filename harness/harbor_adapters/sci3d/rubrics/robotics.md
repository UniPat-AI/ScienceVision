Domain: robotics (URDF/SDF kinematic chains, manipulators, scenes).

Grade with these domain priorities:
- Kinematic correctness: joint types, axes, link hierarchy, and reachability
  claims must match the actual articulated model; penalize inverted parent/
  child relationships and wrong joint axes.
- Pose validity: for trace/manipulation tasks, the proposed poses or
  trajectories must be physically valid (within joint limits, collision-aware
  where stated).
- Efficiency: when efficiency is scored, prefer shorter valid traces; do not
  reward success achieved by exhaustive flailing.
Partial credit when the goal is reached but joint/axis details are imprecise.
