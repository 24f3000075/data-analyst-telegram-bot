import json

import anthropic

from app.config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL, MAX_AGENT_STEPS
from app.logger import RunLogger
from app.tools import ALL_TOOLS, CLIENT_TOOLS, execute_tool

_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

_CLIENT_TOOL_NAMES = {t["name"] for t in CLIENT_TOOLS}

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
- If the data is given inline in the message, use run_python to parse and compute over \
it directly rather than eyeballing it.
- Show your work through tool calls; don't skip straight to a guess.
- Numeric answers should be computed, not estimated, whenever the source data is available.

When you are done and have a final, confident answer, respond with ONLY the JSON value \
that belongs in the "answer" field -- valid JSON, nothing else: no markdown fences, no \
explanation, no surrounding prose, no the word "answer". For example if asked for \
{"answer": {"state": "..."}, ...} and you've determined the state is Assam, your entire \
final message must be exactly:
{"state": "Assam"}
"""


def _content_to_text(content) -> str:
    parts = []
    for block in content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "\n".join(parts)


def _block_to_loggable(block) -> dict:
    btype = getattr(block, "type", "unknown")
    d = {"type": btype}
    if btype == "text":
        d["text"] = block.text
    elif btype == "tool_use":
        d["name"] = block.name
        d["input"] = block.input
        d["id"] = block.id
    elif btype == "server_tool_use":
        d["name"] = getattr(block, "name", None)
        d["input"] = getattr(block, "input", None)
    elif btype == "web_search_tool_result":
        d["content"] = str(getattr(block, "content", ""))[:500]
    return d


def run_agent(messages: list, run_logger: RunLogger) -> dict:
    """
    messages: list of {"role": "user"|"assistant", "content": str} -- full chat
              history for this Telegram chat, last item is the question to answer.
    Returns: {"answer": <parsed json value>, "raw_final_text": str, "error": str|None}
    """
    run_logger.log("agent_start", history=messages)

    api_messages = [{"role": m["role"], "content": m["content"]} for m in messages]

    final_text = None
    for step in range(MAX_AGENT_STEPS):
        try:
            response = _client.messages.create(
                model=ANTHROPIC_MODEL,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                messages=api_messages,
                tools=ALL_TOOLS,
            )
        except Exception as e:  # noqa: BLE001
            run_logger.log("api_error", step=step, error=str(e))
            return {"answer": None, "raw_final_text": "", "error": f"API error: {e}"}

        run_logger.log(
            "model_response",
            step=step,
            stop_reason=response.stop_reason,
            content=[_block_to_loggable(b) for b in response.content],
        )

        if response.stop_reason != "tool_use":
            final_text = _content_to_text(response.content).strip()
            break

        # Append the assistant turn (raw, so tool_use ids line up) and execute
        # any client-side tool calls, then continue the loop.
        api_messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in response.content:
            if getattr(block, "type", None) == "tool_use" and block.name in _CLIENT_TOOL_NAMES:
                try:
                    result = execute_tool(block.name, block.input)
                    result_str = json.dumps(result, default=str)
                    is_error = False
                except Exception as e:  # noqa: BLE001
                    result_str = f"Tool error: {e}"
                    is_error = True

                run_logger.log(
                    "tool_call",
                    step=step,
                    tool=block.name,
                    input=block.input,
                    result=result_str[:3000],
                    is_error=is_error,
                )

                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_str[:6000],
                        "is_error": is_error,
                    }
                )

        if tool_results:
            api_messages.append({"role": "user", "content": tool_results})
        else:
            # stop_reason was tool_use but nothing for us to execute (e.g. only
            # server-side web_search happened) -- ask the model to continue.
            api_messages.append(
                {"role": "user", "content": "Continue."}
            )

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
        # Fall back to treating the raw text as the answer string.
        parsed_answer = final_text

    run_logger.log(
        "agent_final_answer",
        raw_final_text=final_text,
        parsed_answer=parsed_answer,
        parse_error=parse_error,
    )

    return {"answer": parsed_answer, "raw_final_text": final_text, "error": parse_error}
