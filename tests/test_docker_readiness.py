from pathlib import Path


def test_dockerfile_installs_ytdlp():
    """YouTube and Instagram downloads are available in the container image."""
    dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text()
    assert '"yt-dlp>=' in dockerfile
