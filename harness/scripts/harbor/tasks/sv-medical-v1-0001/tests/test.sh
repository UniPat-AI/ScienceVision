#!/bin/bash
# Placeholder. In the host-run route the reward is produced by
# harbor_adapters.sci3d.verifier:Sci3dVerifier (registered via
# --verifier-import-path), not by this in-container script.
# A containerized route would instead call the sci3d scorers here and write
# /logs/verifier/reward.json. See docs/harbor/VERIFY_sci3d.md.
echo "sci3d host-run task: reward emitted by Sci3dVerifier" 1>&2
