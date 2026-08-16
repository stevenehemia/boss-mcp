#include <optional>
#include <string>
#include "handlers.h"
#include "layouter.h"
#include "logger.h"
#include "expression.h"
#include "transport.h"

using json = nlohmann::json;

constexpr const char* defaultProtocolVersion = "2025-11-25";
constexpr const char* serverName = "boss-mcp";
constexpr const char* serverVersion = "0.1.0";


namespace {

json toolResponse(const std::string& text, bool isError) {
  return {{"content", json::array({{{"type", "text"}, {"text", text}}})}, {"isError", isError}};
}

json buildToolsList(const ServerConfig& config) {

  const std::string evaluateDescription =
      config.queryFormat == QueryFormat::ArrayJson
          ? R"(Evaluate a BOSS expression. Call boss_describe first to get the complete operator reference. Operation examples: )"
            R"(Load CSV: ["Load", ["String", "/absolute/path/to/file.csv"]] — always use absolute paths. )"
            R"(In-memory table: ["Table", ["ColName", val1, val2, ...]]. )"
            R"(Column reference: ["Symbol", "colname"]. )"
            R"(Type cast: ["Int", ["Symbol", "col"]]. )"
            R"(Multi-step queries: Name(table, ["Symbol", "label"]) stores a result; ByName(["Symbol", "label"]) retrieves it.)"
          : R"(Evaluate a BOSS expression. Call boss_describe first to get the complete operator reference. Operation examples: )"
            R"(Load CSV: {"type":"call","head":"Load","args":[{"type":"string","value":"/absolute/path/to/file.csv"}]} — always use absolute paths. )"
            R"(In-memory table: {"type":"call","head":"Table","args":[{"type":"call","head":"ColName","args":[v1, v2, ...]}]}. )"
            R"(Column reference: {"type":"symbol","value":"colname"}. )"
            R"(Type cast: {"type":"call","head":"Int","args":[{"type":"symbol","value":"col"}]}. )"
            R"(Multi-step queries: Name(table, {"type":"symbol","value":"label"}) stores a result; ByName({"type":"symbol","value":"label"}) retrieves it.)";

  const std::string expressionDescription =
      config.queryFormat == QueryFormat::ArrayJson
          ? R"(ExpressionJSON array format. )"
            R"(Example (load CSV and filter rows where code = "GBR"): )"
            R"(["Filter", ["Load", ["String", "/absolute/path/to/data.csv"]], ["Equal", ["Symbol", "code"], ["String", "GBR"]]])"
          : R"(BOSS expression as a nested JSON object with "type", "head", and "args" fields. )"
            R"(Column references use {"type":"symbol","value":"colname"}. )"
            R"(Example (load CSV and filter rows where code = "GBR"): )"
            R"({"type":"call","head":"Filter","args":[{"type":"call","head":"Load","args":[{"type":"string","value":"/absolute/path/to/data.csv"}]},{"type":"call","head":"Equal","args":[{"type":"symbol","value":"code"},{"type":"string","value":"GBR"}]}]})";

  const char* expressionType = config.queryFormat == QueryFormat::ArrayJson ? "array" : "object";

  json properties = {
      {"expression", {{"type", expressionType}, {"description", expressionDescription}}}};

  // Auto mode specific features
  std::string paginationNote;
  if(config.resultFormat == ResultFormat::Auto) {
    properties["response_intent"] = {
        {"type", "string"},
        {"enum", json::array({"lookup", "extremum", "aggregate"})},
        {"description",
         "Optional. What you intend to do with the result, so the server can pick the "
         "layout that is easiest to read for that: 'lookup' to find the value at a "
         "specific row, 'extremum' to find a maximum or minimum, 'aggregate' to compute "
         "over all rows. Omit if none applies."}};

    paginationNote =
        config.queryFormat == QueryFormat::ArrayJson
            ? R"( The result may come back paginated as )"
              R"({"table":..., "overbudget_row_count":N} instead of the full data. If )"
              R"(overbudget_row_count is greater than 0, that many rows were withheld -- fetch )"
              R"(them with ["Slice", <expression>, ["Int", <rows of this query you already )"
              R"(have>], ["Int", overbudget_row_count]] -- offset/count must use "Int" (32-bit); )"
              R"("Integer" is rejected.)"
            : R"( The result may come back paginated as )"
              R"({"table":..., "overbudget_row_count":N} instead of the full data. If )"
              R"(overbudget_row_count is greater than 0, that many rows were withheld -- fetch )"
              R"(them with {"type":"call","head":"Slice","args":[<expression>, )"
              R"({"type":"int","value":<rows of this query you already have>}, )"
              R"({"type":"int","value":overbudget_row_count}]} -- offset/count must use "int" )"
              R"((32-bit); "long" is rejected.)";
  }

  json evaluateTool;
  evaluateTool["name"] = "boss_evaluate";
  evaluateTool["description"] = evaluateDescription + paginationNote;
  evaluateTool["inputSchema"] = {{"type", "object"},
                                 {"properties", properties},
                                 {"required", json::array({"expression"})}};

  // Advertise boss_evaluate character budget to the host (correspond to tool output cap)
  // Claude specific spec
  if(config.maxResultSizeChars > 0) {
    evaluateTool["_meta"] = {{"anthropic/maxResultSizeChars", config.maxResultSizeChars}};
  }

  json describeTool;
  describeTool["name"] = "boss_describe";
  describeTool["description"] =
      "Returns the complete BOSS operator reference directly from the engine. "
      "Call this before boss_evaluate to discover all available operations and their usage.";
  describeTool["inputSchema"] = {{"type", "object"}, {"properties", json::object()}};

  return json::array({evaluateTool, describeTool});
}


json handleDescribeCall(const LogLevel& logLevel) {

  SymbolPtr sym(symbolNameToNewBOSSSymbol("GetEngineDescription"));
  ExprPtr expr(newComplexBOSSExpression(sym.get(), 0, nullptr));

  std::string description;

  try {
    // BOSSEvaluate consumes its input expression.
    ExprPtr result(BOSSEvaluate(expr.release()));
    description = toString(getNewStringValueFromBOSSExpression(result.get()));
  } catch(...) {
    return toolResponse("GetEngineDescription failed", true);
  }

  if(description.empty()) {
    return toolResponse("engine description unavailable", true);
  }

  logMessage(logLevel, LogLevel::Debug, "boss_describe called");

  return toolResponse(description, false);
}


json handleToolsCall(const json& params, const LogLevel& logLevel,
                     const ServerConfig& config) {

  if(!params.contains("name") || !params["name"].is_string()) {
    return toolResponse("missing tool name", true);
  }

  const std::string name = params["name"].get<std::string>();
  if(name == "boss_describe") {
    return handleDescribeCall(logLevel);
  }
  if(name != "boss_evaluate") {
    return toolResponse("unknown tool", true);
  }

  if(!params.contains("arguments") || !params["arguments"].is_object()) {
    return toolResponse("missing arguments", true);
  }

  const json& arguments = params["arguments"];

  if(!arguments.contains("expression")) {
    return toolResponse("missing expression", true);
  }

  std::string error;
  ExprPtr expression = parseExpression(arguments["expression"], config.queryFormat, error);

  if(!expression) {
    return toolResponse(error, true);
  }

  // Captured before BOSSEvaluate consumes the expression
  // If the agent calls for Slice(inner, offset, count),
  // IndexedColumnarJson's row labels should continue from that offset
  // rather than restarting at 0
  const size_t labelOffset = detectSliceOffset(expression.get()).value_or(0);

  // On a follow-up Slice, cost the layout against the full inner table
  // rather than the truncated page this call returns
  // Otherwise the winning format can change mid-retrieval
  ExprPtr innerResult;
  std::optional<ColumnData> decisionTable;
  if(auto innerJson = detectSliceInnerJson(arguments["expression"], config.queryFormat)) {
    std::string innerError;
    if(ExprPtr innerExpr = parseExpression(*innerJson, config.queryFormat, innerError)) {
      try {
        innerResult.reset(BOSSEvaluate(innerExpr.release()));
        decisionTable = extractTable(innerResult.get());
      } catch(...) {
        // If re-evaluating `inner` fails for any reason, fall back
        // to costing against the actually-served (sliced) table
        innerResult.reset();
        decisionTable.reset();
      }
    }
  }

  std::string resultText;
  std::string layoutNote;

  try {
    // BOSSEvaluate consumes its input expression.
    ExprPtr result(BOSSEvaluate(expression.release()));

    if(config.resultFormat == ResultFormat::Auto) {
      // Auto resolves per query, on the materialized result. A non-Table (a
      // scalar, an error expression) has no layout to choose between and falls
      // through to the general typed encoding, same as every fixed format does.
      if(std::optional<ColumnData> table = extractTable(result.get())) {
        CostConfig costConfig;
        costConfig.budgetChars = config.maxResultSizeChars;
        costConfig.defaultThinking = config.defaultThinking;
        
        LayoutDecision decision =
            chooseLayout(*table, costConfig,
                         parseTaskHint(arguments.value("response_intent", "")),
                         labelOffset, decisionTable ? &*decisionTable : nullptr);

        const Delivery delivery = decision.delivery;
        resultText = std::move(decision.text);
        switch(delivery) {
          case Delivery::Oversized:
            // Not even a single row fits within a page, still send the whole result
            layoutNote = "result is " + std::to_string(resultText.size()) +
                         " characters, over the advertised budget of " +
                         std::to_string(config.maxResultSizeChars) +
                         "; the host may persist it to disk rather than return it inline";
            break;
          case Delivery::Paged:
            // The intended outcome of a query result that doesn't fit
            // within tool token cap, served as a bounded first page instead
            // of the full result
            logMessage(logLevel, LogLevel::Info,
                       "result paginated to fit budget of " +
                           std::to_string(config.maxResultSizeChars) + " characters");
            break;
          case Delivery::Whole:
            break;
        }
      } else {
        resultText = toTypedColumnarJson(result.get()).dump();
      }
    } else {
      resultText = expressionToJson(result.get(), config.resultFormat, labelOffset).dump();
    }
  } catch(const std::exception& e) {
    return toolResponse(std::string("BOSS evaluation error: ") + e.what(), true);
  } catch(...) {
    return toolResponse("BOSS evaluation error: unknown exception", true);
  }

  logMessage(logLevel, LogLevel::Debug, "Evaluated expression using boss_evaluate");
  if(!layoutNote.empty()) {
    logMessage(logLevel, LogLevel::Warn, layoutNote);
  }
  return toolResponse(resultText, false);
}

}  // namespace


bool handleRequest(const json& request, LogLevel& logLevel, const ServerConfig& config) {

  const std::string method = request.value("method", "");

  if(method == "initialized" || method == "notifications/initialized") return false;

  if(method == "exit") return true;

  if(!request.contains("id")) return false;

  const json id = request["id"];

  if(method == "shutdown") {
    sendResponse(makeResult(id, nullptr));
    return true;
  }

  if(method == "initialize") {
    std::string protocolVersion = defaultProtocolVersion;
    if(request.contains("params") && request["params"].is_object()) {
      const json& params = request["params"];
      if(params.contains("protocolVersion") && params["protocolVersion"].is_string()) {
        protocolVersion = params["protocolVersion"].get<std::string>();
      }
    }
    json result;
    result["protocolVersion"] = protocolVersion;
    result["serverInfo"] = {{"name", serverName}, {"version", serverVersion}};
    result["capabilities"] = {{"tools", {{"listChanged", false}}}, {"logging", json::object()}};
    sendResponse(makeResult(id, result));
    return false;
  }

  if(method == "logging/setLevel") {
    if(request.contains("params") && request["params"].is_object()) {
      logLevel = parseLogLevel(request["params"].value("level", "info"));
    }
    sendResponse(makeResult(id, json::object()));
    return false;
  }

  if(method == "tools/list") {
    sendResponse(makeResult(id, {{"tools", buildToolsList(config)}}));
    return false;
  }

  if(method == "tools/call") {
    if(!request.contains("params") || !request["params"].is_object()) {
      sendResponse(makeError(-32602, "Missing params", id));
      return false;
    }
    sendResponse(makeResult(id, handleToolsCall(request["params"], logLevel, config)));
    return false;
  }

  sendResponse(makeError(-32601, "Method not found", id));
  return false;
}
