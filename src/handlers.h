#pragma once
#include <cstddef>
#include "expression.h"
#include "layouter.h"
#include "logger.h"
#include "nlohmann/json.hpp"


struct ServerConfig {
  QueryFormat queryFormat = QueryFormat::ArrayJson;
  ResultFormat resultFormat = ResultFormat::ColumnarJson;
  // Advertised to the host in tools/list as
  // `_meta["anthropic/maxResultSizeChars"]`,
  // 0 advertises nothing, disable the pagination mechanism,
  // and leaves to the host's default (~50k chars for
  // Claude Code's 25k-token cap)
  size_t maxResultSizeChars = 0;
  // layouter's inputs under --result-format=auto
  // main.cpp seeds budgetChars and defaultThinking after flag parsing
  // The cost model's calibrated tables are held here
  CostConfig cost;
};

bool handleRequest(const nlohmann::json& request, LogLevel& logLevel,
                   const ServerConfig& config);
