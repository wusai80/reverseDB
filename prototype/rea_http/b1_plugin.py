import pytest


def pytest_addoption(parser):
    parser.addoption("--b1-tags", action="store_true")


@pytest.fixture(autouse=True)
def apply_candidate(request):
    from b1_permissions import candidate
    with candidate(request.config.getoption("--b1-tags")):
        yield
