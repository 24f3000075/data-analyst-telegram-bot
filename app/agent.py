import json
import re
import time

from google import genai
from google.genai import types

from app.config import GEMINI_API_KEY, GEMINI_MODEL, MAX_AGENT_STEPS
from app.logger import RunLogger
from app.tools import TOOL_SPECS, execute_tool

_client = genai.Client(api_key=GEMINI_API_KEY)
_MAX_RETRIES_PER_STEP = 5

_FUNCTION_DECLARATIONS = [
    types.FunctionDeclaration(
        name=spec["name"],
        description=spec["description"],
        parameters=types.Schema(**spec["parameters"]),
    )
    for spec in TOOL_SPECS
]
_GEMINI_TOOLS = [types.Tool(function_declarations=_FUNCTION_DECLARATIONS)]
_TOOL_NAMES = {spec["name"] for spec in TOOL_SPECS}

SYSTEM_PROMPT = """You are a meticulous data-analyst agent. You receive a data-analysis \
question (often referencing MOSPI or another public Indian/global statistics dataset, \
sometimes with the data given inline in the message). Some conversations are multi-turn: \
earlier user turns give context, and you must answer the *final* user message.

The user's message will describe exactly what JSON shape their "answer" field should be \
(for example {"state": "..."} or a number or a list). It will also show a "log_url" \
placeholder in an example JSON -- IGNORE that placeholder entirely, it is not your \
concern; your job is only to produce the value for the "answer" field, correctly shaped.

Work the problem for real:
- If the question references a public dataset (MOSPI, data.gov.in, etc.) and doesn't \
paste the data inline, use web_search to find the actual dataset/report page, then \
fetch_url to download the actual file (CSV/XLSX/PDF/HTML), then run_python (pandas is \
available) to compute the precise answer from real numbers. Do not guess or rely on \
general knowledge for anything a dataset could answer more precisely.
- web_search can be unreliable. If it errors or returns nothing useful, do NOT retry it \
more than once -- immediately fall back to run_python with the `requests` library to \
fetch a known reference (Wikipedia, data.gov.in, mospi.gov.in, press releases, PIB) \
directly, or fetch_url a specific URL you already know or can construct. This fallback \
path is reliable; use it rather than looping on web_search.
- If the data is given inline in the message, use run_python to parse and compute over \
it directly rather than eyeballing it.
- Show your work through tool calls; don't skip straight to a guess.
- Numeric answers should be computed, not estimated, whenever the source data is available.
- Be efficient: each tool call costs time and a limited API quota. Don't repeat a nearly \
identical call twice in a row -- if an approach fails once, switch strategy rather than \
retrying the same thing.
- Prefer STRUCTURED sources over many narrow web_search queries. If web_search returns \
empty or unhelpful results a couple of times, stop refining the search phrase and instead \
go straight for a known structured reference: fetch a relevant Wikipedia article with \
run_python + requests + pandas.read_html(...) (Wikipedia articles on Indian states/health \
statistics usually have a clean wikitable with exactly the numbers you need), or try \
data.gov.in / mospi.gov.in directly. One well-chosen structured fetch beats five vague \
searches. Budget your steps: aim to have real data in hand within the first 4-6 tool \
calls, not the last few.
- Don't be a perfectionist about sourcing once you have a good answer. If 2+ independent, \
credible sources (news outlets, government press summaries, etc.) already agree on a \
figure, that is sufficient -- stop and answer. Do not keep hunting for the exact primary \
PDF/press-release URL once corroborated data is already in hand; that wastes your step \
budget on verification that doesn't change the answer.

When you are done and have a final, confident answer, respond with ONLY the JSON value \
that belongs in the "answer" field -- valid JSON, nothing else: no markdown fences, no \
explanation, no surrounding prose, no the word "answer", and CRITICALLY no "log_url" key \
of any kind -- you have no log_url and must never invent or include one. For example if \
asked for {"answer": {"state": "..."}, ...} and you've determined the state is Assam, \
your entire final message must be exactly:
{"state": "Assam"}
Do NOT write {"answer": {"state": "Assam"}, "log_url": "..."} -- that is wrong, it \
duplicates the wrapper the calling code already adds and will break the response.
"""


def _extract_retry_delay(error_message: str) -> float:
    match = re.search(r"retry(?:Delay)?['\"]?\s*[:=]?\s*['\"]?(\d+(?:\.\d+)?)s?", error_message, re.IGNORECASE)
    if match:
        return float(match.group(1)) + 2
    return 20.0


def _is_rate_limit_error(error_message: str) -> bool:
    return "429" in error_message or "RESOURCE_EXHAUSTED" in error_message


def _history_to_contents(messages: list) -> list:
    contents = []
    for m in messages:
        role = "model" if m["role"] == "assistant" else "user"
        contents.append(types.Content(role=role, parts=[types.Part.from_text(text=m["content"])]))
    return contents


def _part_to_loggable(part) -> dict:
    if part.text is not None:
        return {"type": "text", "text": part.text}
    if part.function_call is not None:
        return {
            "type": "function_call",
            "name": part.function_call.name,
            "args": dict(part.function_call.args or {}),
        }
    return {"type": "unknown"}


def run_agent(messages: list, run_logger: RunLogger) -> dict:
    run_logger.log("agent_start", history=messages)

    contents = _history_to_contents(messages)
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        tools=_GEMINI_TOOLS,
        tool_config=types.ToolConfig(
            function_calling_config=types.FunctionCallingConfig(mode="AUTO")
        ),
    )

    final_text = None
    for step in range(MAX_AGENT_STEPS):
        response = None
        for attempt in range(_MAX_RETRIES_PER_STEP):
            try:
                response = _client.models.generate_content(
                    model=GEMINI_MODEL, contents=contents, config=config
                )
                break
            except Exception as e:
                err_str = str(e)
                if _is_rate_limit_error(err_str) and attempt < _MAX_RETRIES_PER_STEP - 1:
                    delay = _extract_retry_delay(err_str)
                    run_logger.log(
                        "rate_limited_retry",
                        step=step,
                        attempt=attempt,
                        delay_seconds=delay,
                        error=err_str,
                    )
                    time.sleep(delay)
                    continue
                run_logger.log("api_error", step=step, error=err_str)
                return {"answer": None, "raw_final_text": "", "error": f"API error: {e}"}

        candidate = response.candidates[0] if response.candidates else None
        parts = candidate.content.parts if candidate and candidate.content else []

        run_logger.log(
            "model_response",
            step=step,
            content=[_part_to_loggable(p) for p in parts],
        )

        function_calls = [p for p in parts if p.function_call is not None]

        if not function_calls:
            final_text = "".join(p.text for p in parts if p.text is not None).strip()
            break

        contents.append(types.Content(role="model", parts=parts))

        response_parts = []
        for part in function_calls:
            name = part.function_call.name
            args = dict(part.function_call.args or {})
            if name not in _TOOL_NAMES:
                result = {"error": f"Unknown tool: {name}"}
                is_error = True
            else:
                try:
                    result = execute_tool(name, args)
                    is_error = False
                except Exception as e:
                    result = {"error": str(e)}
                    is_error = True

            run_logger.log(
                "tool_call",
                step=step,
                tool=name,
                input=args,
                result=json.dumps(result, default=str)[:3000],
                is_error=is_error,
            )

            response_parts.append(
                types.Part.from_function_response(name=name, response=result)
            )

        contents.append(types.Content(role="user", parts=response_parts))

    if final_text is None:
        run_logger.log("agent_max_steps_reached")
        return {
            "answer": None,
            "raw_final_text": "",
            "error": "Max tool-use steps reached without a final answer.",
        }

    parsed_answer = None
    parse_error = None
    cleaned = final_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    try:
        parsed_answer = json.loads(cleaned)
    except json.JSONDecodeError as e:
        parse_error = str(e)
        parsed_answer = final_text

    if isinstance(parsed_answer, dict) and "answer" in parsed_answer:
        parsed_answer = parsed_answer["answer"]

    run_logger.log(
        "agent_final_answer",
        raw_final_text=final_text,
        parsed_answer=parsed_answer,
        parse_error=parse_error,
    )

    return {"answer": parsed_answer, "raw_final_text": final_text, "error": parse_error}
