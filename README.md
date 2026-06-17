# boss-mcp

An MCP (Model Context Protocol) server built in C++ that integrates BOSS — a homoiconic, Lisp-based database management system — with AI agents such as Claude.

The project is part of my MSc Computing dissertation at Imperial College London, researching whether homoiconic data representations can make agentic AI workflows more reliable, controllable, and cost-efficient.

---

## Current findings

Benchmarked across 18 natural-language queries on a 175 MB real-world dataset (OWID COVID-19, 573k rows, 61 columns), comparing a BOSS-backed agent against a conventional tool-use baseline:

| Metric | BOSS | Baseline | Ratio |
|---|---|---|---|
| Total running cost | $1.61 | $2.49 | **0.65×** |
| Total input tokens consumed | 1.16M | 2.68M | **0.43×** |
| Mean turns per question | 3.3 | 5.8 | **0.57×** |
| Mean duration per question | 41 s | 49 s | **0.84×** |
| Agent permission failures | 0 | 10 | — |

**57% fewer tokens consumed. 35% lower cost. Zero permission failures vs 10 in the baseline.**

The saving is driven by two mechanisms: (1) BOSS's homoiconic ExpressionJSON query format is compact relative to multi-turn CSV introspection, and (2) the BOSS agent executes in a single MCP call per question rather than iterating over tool outputs across multiple turns. The baseline agent's turn count was highly variable (2–23 turns); BOSS was consistently 3–5 turns across all query types.

---

## Background

BOSS is a database management system that uses homoiconic S-expressions (similar to Lisp) as its data model. Because code and data share the same representation, BOSS enables powerful in-database computation and is particularly well-suited for structured, auditable reasoning over data.

Current agentic AI workflows suffer from reliability issues: agents lose context, accumulate tokens across iterative tool calls, make uncontrolled state changes, or fail unpredictably when interacting with large external datasets. BOSS's homoiconic structure offers a way to represent queries and results in a form that is compact, transparent, and easy for agents to reason over in a single call.

This MCP server bridges BOSS and AI agents — allowing an LLM to query and manipulate BOSS through structured tool calls, enabling more controlled, efficient, and reliable agentic behaviour.

---

## Technical Overview

- **Language:** C++ (C++17/20 — templates, RAII, modern memory management)
- **Evaluation scripts:** Python
- **Build system:** CMake
- **Protocol:** Model Context Protocol — JSON-RPC over stdio
- **Integration:** Compatible with Claude and other MCP-capable LLM clients

### Repository Structure

```
boss-mcp/
├── src/          # C++ MCP server implementation
├── external/     # External dependencies
├── test/         # Test suite
├── CMakeLists.txt
└── build.sh      # Build script
```

### Building

```bash
./build.sh
```

Or manually with CMake:

```bash
mkdir build && cd build
cmake ..
cmake --build .
```

---

## Research Goals

- Evaluate whether homoiconic data representations improve agent reliability and context efficiency versus conventional file-based tool use
- Measure the cost and token impact of BOSS-backed agents on realistic analytical queries across different result shapes
- Explore structured tool-calling patterns that reduce unintended side effects and permission failures in agentic workflows

---

## Status

Active research project — codebase evolving alongside the dissertation (MSc Computing, Imperial College London, 2025–2026).
