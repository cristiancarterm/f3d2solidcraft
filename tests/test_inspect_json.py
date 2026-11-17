"""What the file holds, as the desktop app's first step shows it: counts, and no prediction."""

import json

from f3d2solidcraft.cli import main


def test_inspect_json_counts_what_the_sample_holds(tools, sample_f3d, capsys):
    assert main(["inspect", str(sample_f3d), "--json"]) == 0
    counts = json.loads(capsys.readouterr().out.strip())
    # The public sample: 32 parameters, 8 sketches, 26 features (measured with `inspect`).
    assert counts == {"parameters": 32, "sketches": 8, "features": 26}
