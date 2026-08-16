#pragma once
#include <cstddef>
#include "expression.h"
#include "logger.h"
#include "nlohmann/json.hpp"


struct ServerConfig {
  QueryFormat queryFormat = QueryFormat::ArrayJson;
  ResultFormat resultFormat = ResultFormat::ColumnarJson;
  // Advertised to the host in tools/list as `_meta["anthropic/maxResultSizeChars"]`,
  // and passed to the layouter as CostConfig::budgetChars under
  // --result-format=auto. Clamped to the host ceiling by main.cpp before it
  // gets here, so it is always a value the host will honour.
  //
  // 0 advertises nothing and leaves the host's own default in force (~50k chars
  // for Claude Code's 25k-token cap). Note this disables the layouter's budget
  // as well: it won't paginate against that still-enforced cap, so an oversized
  // result gets served whole and spilled to disk by the host.
  size_t maxResultSizeChars = 0;
  bool defaultThinking = false;
};

bool handleRequest(const nlohmann::json& request, LogLevel& logLevel,
                   const ServerConfig& config);
