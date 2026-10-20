import pytest
from sci3d_agent.kernel import FakeKernel, KernelTimeout, KernelCrash

@pytest.mark.unit
def test_fake_kernel_executes_simple_code():
    k = FakeKernel()
    result = k.execute("x = 1+1")
    assert result["stdout"] == ""
    assert result["images"] == []

@pytest.mark.unit
def test_fake_kernel_persists_state_across_calls():
    k = FakeKernel()
    k.execute("x = 42")
    result = k.execute("print(x)")
    assert "42" in result["stdout"]

@pytest.mark.unit
def test_fake_kernel_simulates_timeout():
    k = FakeKernel(simulate_timeout_on=("SLEEP",))
    with pytest.raises(KernelTimeout):
        k.execute("SLEEP")

@pytest.mark.unit
def test_fake_kernel_simulates_image_output():
    k = FakeKernel(emit_image_on=("plt.savefig",))
    result = k.execute("plt.savefig('a.png')")
    assert len(result["images"]) == 1

@pytest.mark.unit
def test_fake_kernel_restart_clears_state():
    k = FakeKernel()
    k.execute("x = 99")
    k.restart()
    result = k.execute("print(x)")
    assert "NameError" in result["stderr"]

@pytest.mark.unit
def test_fake_kernel_simulates_crash():
    k = FakeKernel(crash_on=("CRASH",))
    with pytest.raises(KernelCrash):
        k.execute("CRASH")

@pytest.mark.unit
def test_fake_kernel_exec_count_increments_per_call():
    k = FakeKernel()
    r1 = k.execute("pass")
    r2 = k.execute("pass")
    r3 = k.execute("pass")
    assert (r1["exec_count"], r2["exec_count"], r3["exec_count"]) == (1, 2, 3)

@pytest.mark.unit
def test_fake_kernel_restart_resets_exec_count():
    k = FakeKernel()
    k.execute("pass")
    k.execute("pass")
    k.restart()
    r = k.execute("pass")
    assert r["exec_count"] == 1
