import os
import pytest

REQUIRES_DOCKER = pytest.mark.skipif(os.environ.get("RUN_DOCKER_TESTS") != "1",
                                     reason="set RUN_DOCKER_TESTS=1 to enable")

@pytest.mark.integration
@REQUIRES_DOCKER
def test_docker_kernel_executes_and_persists_state():
    pytest.importorskip("jupyter_client")
    from sci3d_agent.kernel import JupyterDockerKernel
    k = JupyterDockerKernel()
    try:
        k.start()
        k.execute("x = 17")
        result = k.execute("print(x * 2)")
        assert "34" in result["stdout"]
    finally:
        k.shutdown()

@pytest.mark.integration
@REQUIRES_DOCKER
def test_docker_kernel_timeout():
    pytest.importorskip("jupyter_client")
    from sci3d_agent.kernel import JupyterDockerKernel, KernelTimeout
    k = JupyterDockerKernel()
    try:
        k.start()
        with pytest.raises(KernelTimeout):
            k.execute("import time; time.sleep(120)", timeout_s=2)
    finally:
        k.shutdown()
