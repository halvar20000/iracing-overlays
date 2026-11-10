"""Prompter file choice: follows the iRacing track, a hand choice stays until
the track changes, a matching file added later (update) is taken."""
import sys
import tempfile
from pathlib import Path

import prompter

PASS = FAIL = 0


def check(name, cond, info=""):
    global PASS, FAIL
    PASS += bool(cond)
    FAIL += not cond
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f" {info}"))


with tempfile.TemporaryDirectory() as tmp:
    d = Path(tmp)
    for f in ("hungaroring-stream-notes-de.md", "indianapolis-stream-notes-de.md"):
        (d / f).write_text("# X\n\n- eins\n- zwei\n", encoding="utf-8")
    track = ["Hungaroring"]
    p = prompter.Prompter(d, lambda: track[0])
    check("P1 picked for the track", p.state()["file"] == "hungaroring-stream-notes-de.md", p.state())
    track[0] = "Circuit de Spa-Francorchamps"
    check("P2 no Spa file yet: stays", p.state()["file"] == "hungaroring-stream-notes-de.md", p.state())
    (d / "spa-francorchamps-stream-notes-de.md").write_text("# Spa\n\n- a\n- b\n", encoding="utf-8")
    check("P3 Spa file arrives: taken", p.state()["file"] == "spa-francorchamps-stream-notes-de.md", p.state())
    p.command("next")
    p.command("file", "indianapolis-stream-notes-de.md")
    check("P4 hand choice kept", p.state()["file"] == "indianapolis-stream-notes-de.md", p.state())
    p.state()
    check("P5 still kept on the next poll", p.state()["file"] == "indianapolis-stream-notes-de.md")
    track[0] = "Hungaroring"
    check("P6 track change = follows again", p.state()["file"] == "hungaroring-stream-notes-de.md", p.state())
    track[0] = ""
    check("P7 no track (iRacing off) = file kept", p.state()["file"] == "hungaroring-stream-notes-de.md")

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
