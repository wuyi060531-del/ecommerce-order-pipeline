"""The static demo under docs/demo must not drift from what the real pipeline produces."""
from scripts import build_demo_data


def test_committed_demo_data_and_embedded_page_match_the_real_pipeline(capsys):
    assert build_demo_data.main(["--check"]) == 0, capsys.readouterr().err
