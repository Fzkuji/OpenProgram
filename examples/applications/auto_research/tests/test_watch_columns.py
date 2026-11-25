"""watch.csv keeps the columns the author or other tasks added."""

from auto_research.tasks.watch import workflow as watch


def test_extra_columns_survive_a_save(tmp_path):
    folder = tmp_path / "workflow"
    folder.mkdir()
    (folder / "watch.csv").write_text("id,title,url,date,claim,verdict,reason,handled,score,mine\n"
                                      "arXiv:1,T,u,2026,C1,related,r,,0.91,yes\n", encoding="utf-8")
    rows = watch.load(tmp_path)
    assert rows[0]["score"] == "0.91" and rows[0]["mine"] == "yes"
    rows.append({k: "" for k in watch.COLUMNS} | {"id": "arXiv:2"})
    watch.save(tmp_path, rows)
    text = (folder / "watch.csv").read_text(encoding="utf-8").splitlines()
    assert text[0].endswith("handled,score,mine") and text[1].endswith(",0.91,yes") and text[2].endswith(",,")
