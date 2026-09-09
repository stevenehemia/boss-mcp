# boss-mcp

An MCP (Model Context Protocol) server in C++ that exposes
[BOSS](https://github.com/symbol-store/BOSS) — a homoiconic, composable DBMS —
to AI agents such as Claude.

The server implements five serialisations of the same table and a **layouter**
that picks between them per result to answer a question: how should a database
engine serialise its query result for reliable and token efficient delivery
to an AI agent.

| format | shape | analogue |
|---|---|---|
| `columnarjson` | `["col", v1, v2, …], …` | DSM |
| `indexedcolumnarjson` | `["col", [0, v1], [1, v2], …]` | DSM + row tags |
| `positionalrowsjson` | schema once, then value-tuples | NSM |
| `arrayofobjectsjson` | one object per row, keys repeated | REST convention |

---

## Server usage

Two tools:
`boss_describe` (forward GetEngineDescription from the compute engine) and
`boss_evaluate` (evaluates an expression and returns the result).

```bash
build/boss_mcp \
  --query-format=arrayjson \        # or objectjson
  --result-format=auto \            # or any of the five layouts
  --default-thinking=on \           # which accuracy row the layouter uses
  --max-result-size-chars=200000    # raise the host's per-result cap
```

### Building

```bash
./build.sh
```

Requires `clang`/`clang++` and a sibling BOSS checkout at `../BOSS` with
`../BOSS/Build/libBOSS.so` already built.

---

## Evaluation suite

Three benchmark harnesses to characterise the properties of each data
representation format and the effectiveness of the **layouter**.

| directory | measures |
|---|---|
| **`efficiency/`** | live-agent cost: turns, retries, size limit breaches |
| **`accuracy/`** | how the formats affect agent's reasoning accuracy |
| **`token_cost/`** | offline token cost of each format, no live-agent |

### Prerequisites

Build the server first (`./build.sh`) — every study shells out to
`build/boss_mcp`. All evaluations query one dataset, not in the repository
(175 MB, gitignored).

Download the [Our World in Data COVID-19 dataset](https://github.com/owid/covid-19-data/tree/master/public/data)
— 591,522 rows × 61 columns of daily per-country series — and save it under
`data/` directory at the repository root:

```
boss-mcp/
├── data/
│   └── owid-covid-data-full.csv    <- here
├── eval/
└── src/
```

---

## Repository layout

```
boss-mcp/
├── src/              # C++ MCP server
│   ├── expression.*  # ExpressionJSON ⇄ BOSS, five result serialisations
│   ├── layouter.*    # the cost function and format decision
│   └── handlers.*    # MCP dispatch, tool definitions
├── eval/             # five studies (see below)
├── test/             # protocol and layouter tests
└── build.sh
```