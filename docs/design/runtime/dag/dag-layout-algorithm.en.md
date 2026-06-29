# DAG Layout Algorithm Design Doc

Status: **decided (visual spec is finalized, implement accordingly)** · Created: 2026-06-29

> The DAG minimap on the right places each node at a grid (column, row). This doc is the **authoritative implementation standard**: all layout code from now on is written to match it, and when something breaks, check it against this doc (including the 7 figures in `dag-layout-spec.html`).
>
> **Visual spec (must read)**: `docs/design/runtime/dag-layout-spec.html` — it draws the target result for 7 scenarios using real SVG. What the code renders must match those 7 figures exactly. This doc is the text version of the rules; that html is the illustrated version, and the two are equivalent.

## 1. A node's position = (column, row)

- **Column (horizontal) = lane's starting column + tier indent**
- **Row (vertical) = depth**

Three quantities: **lane** (which branch it belongs to), **tier** (how many cells it is indented within the branch), **depth** (which row). Each is defined below.

## 2. lane — which branch this node belongs to

**Rule: there are as many columns as there are branches. Number them in the order the branches appear, starting from 0, with no gaps.**

- 1st branch → lane 0
- 2nd branch → lane 1
- 3rd branch → lane 2 ……

**A branch** = a conversation chain: it starts from one user message and follows the conversation downward (user → AI reply → next user → AI reply …) as a single continuous chain. Retrying / rewriting a message partway through **forks a new branch** at that point, and the new branch takes the next number.

| Scenario | Branch count | lane |
|---|---|---|
| Sending just "hello" (no retry) | 1 | all lane 0, no 2nd column |
| "hello" + retry rewritten to "you there" | 2 | original lane 0, the retry produces lane 1 |

**Never do** (the detours the old code took, all removed):
- ❌ specially deciding "which one is the main trunk"
- ❌ special handling like "reset to zero"
- ❌ letting a conversation with no fork occupy the 2nd column

It is simply: **count the branches, assign column numbers in order of appearance.**

## 3. tier — how many cells to indent rightward within the branch

**Rule: indent by a fixed amount per role, expressing "who is nested inside whom" (following `caller` sub-call edges).**

| Node | tier |
|---|---|
| ROOT | 0 |
| user | 1 |
| AI reply (llm) | 2 |
| tool the AI calls (code) | 3 |
| what a tool itself calls (sub-call) | parent +1 |

The "staircase going right" indent effect is **correct**, not a bug. The next round's user returns to tier 1 (it does not keep climbing rightward) — because all top-level users hang directly under ROOT, at the same level.

## 4. depth — which row, top to bottom

**Rule: order by chat/execution sequence (`predecessor` conversation chain + seq), top to bottom.**

- The earlier something happened, the higher up it is.
- A forked branch **aligns to the same row** as the position it forked from to start, then each grows downward on its own.

## 5. Three global layout rules

**① Square grid**: horizontal cell spacing = vertical cell spacing (`COL_W == ROW_H`). The whole figure is a standard checkerboard, with column spacing and row spacing perfectly uniform. A child node sits at the **strict bottom-right** of its parent — one cell right + one cell down (a 45° diagonal, distance √2× the cell spacing).

**② Strict grid alignment + compact in both directions**: every node lands on a grid intersection, with y perfectly aligned across the same row and x perfectly aligned across the same column, no half-cell offsets allowed. **When a column empties, the columns on the right shift left to fill it; when a row empties, the rows below shift up to fill it** — collapse a node and the row it occupied empties out, so all rows below shift up as a block; the column it occupied empties out, so the columns to the right shift left. No empty rows or empty columns are kept.

**③ Branches are packed tight by the columns they actually occupy, with no overlap**: the columns a branch occupies = from its starting column to the deepest cell of its subtree. **The next branch starts at the previous branch's rightmost occupied column +1**, sitting entirely to the right of the previous one, with no overlap.

> Rules ② and ③ together = "collapse and shift left": collapse a sub-call of the previous branch → it occupies fewer columns → the next branch's starting column shifts left accordingly. Example: lane0 expanded occupies columns 0~3, lane1 starts at column 4; after the tool is collapsed, lane0 occupies only 0~2, lane1 shifts left to column 3, and the rows below shift up at the same time.

## 6. What it looks like rendered (text version; see spec.html for figures)

**Sending just "hello" (1 branch, all lane 0):**
```
col:  0      1        2
row0 ◇ROOT
row1        ○hello              ← lane0, tier1
row2                 △AI reply  ← lane0, tier2
```

**"hello" + retry rewritten to "you there" (2 branches):**
```
col:  0       1        2        3            4
row0 ◇ROOT
row1        ○hello ┈┈┈┈┈┈┈┈┈ ○you there            ← lane1 starts at column 3 (lane0 occupies columns 0~2)
row2                 △reply1            △reply1'
```
lane0 occupies columns 0~2 → lane1 starts at column 3, packed tight with no overlap. The dashed line = parallel branches forked from the same point.

> For all 7 scenarios (single-turn / multi-turn / retry / tool / manual function call / combined / collapse and shift left), see `dag-layout-spec.html`.

## 7. Current bug (to be fixed)

Sending just "hello" should be all lane 0, but the code assigns **lane 1** to the user/AI reply (skipping 0), causing the whole thing to shift a large chunk to the right and leaving ROOT alone on the left.

Root cause: when deciding "how to split branches into columns", the wrong edge was used — it used `predecessor` (the conversation predecessor), and the first message has no predecessor → can't connect → is misjudged as a new branch; on top of that there was a layer of convoluted "main-trunk decision / reset to zero" logic.

**Fix: rewrite per section 2 — count the branches, assign column numbers 0, 1, 2 in order of appearance, and delete the main-trunk decision and the reset-to-zero.**

## 8. Which files to change

| File | What to change |
|---|---|
| `graph_layout/lane.py` | full rewrite: count branches → assign lane 0, 1, 2 in order of appearance. Delete the ROOT-walk / first_at_fork / reset-to-zero machinery |
| `graph_layout/topology.py` | provide a "same branch chain" check: walk along `caller` (sub-call tree) + `predecessor` (conversation chain) |
| `graph_layout/__init__.py` | branch column-occupancy computation (rule ③): pack tight by each lane's actual max tier, next one = previous one's rightmost column +1 |
| `dag/types.ts` etc. frontend | `COL_W == ROW_H` (square grid, rule ①) |
| Verification | `tools/dag_dump.py` runs the 7 scenarios below |

## 9. Verification (command line, no need to screenshot the web page)

```
python tools/dag_dump.py [session_id]
```
Prints each node's lane / tier / depth + an ASCII grid figure. Run it after the code change to compare.

Scenarios to verify (corresponding to the 7 figures in spec.html):
1. Single-turn: "hello" → all lane 0
2. Multi-turn: all users hang on ROOT, same column at tier1; all lane 0
3. retry: the 2nd branch is lane1, starting at lane0's rightmost column +1
4. Tool call: tool at tier3, indented under the AI, no new lane opened
5. Manual function call: function hangs on ROOT (tier1), internal sub-calls indent level by level
6. Combined: lanes don't overlap, tiers indent, depth ordered by time
7. Collapse: lanes shift left horizontally, rows shift up vertically, no empty rows or empty columns left
