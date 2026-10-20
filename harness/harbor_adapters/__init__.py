"""Harbor adapters: wrap ScienceVision agents as Harbor BaseAgents.

Each subpackage adapts one agent (sci3d today; SWE-Vision and others later)
onto Harbor's BaseAgent contract so a single ``harbor run`` harness can drive
them all.
"""
