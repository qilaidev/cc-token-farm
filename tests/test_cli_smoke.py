from cc_token_farm.cli import main


def test_help():
    try:
        main(["--help"])
    except SystemExit as e:
        assert e.code == 0


def test_version():
    try:
        main(["--version"])
    except SystemExit as e:
        assert e.code == 0


def test_estimate():
    code = main(["estimate", "--tokens", "1M", "-m", "claude-sonnet-5"])
    assert code == 0


def test_models_cheapest():
    code = main(["models", "--cheapest", "--limit", "5"])
    assert code == 0


def test_run_dry():
    code = main(
        [
            "run",
            "--dry-run",
            "-n",
            "2",
            "-m",
            "claude-sonnet-5",
            "-f",
            "anthropic",
            "--prompt-chars",
            "100",
            "-y",
            "-q",
        ]
    )
    assert code == 0
