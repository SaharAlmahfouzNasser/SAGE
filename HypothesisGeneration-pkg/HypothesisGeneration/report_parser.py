import argparse
import json
import re
from pathlib import Path


def parse_log_to_markdown(log_content: str) -> str:
    """
    Parses a log file content and converts it to a markdown format.

    Args:
        log_content: The content of the log file as a string.

    Returns:
        A string in markdown format.
    """
    markdown_lines = ["# Hypothesis generation pipeline summary"]

    # Track state for special handling
    first_user_seen = False
    path_keywords = None

    # Split content by any separator line
    sections = re.split(r"(----------.*?----------)", log_content)

    def _extract_json_objects(s: str):
        """Extract JSON-like substrings that start with { or [ and return them as a list.

        This is a simple stack-based extractor that finds balanced braces/brackets and
        returns the raw substrings. It helps avoid overly greedy regex matches.
        """
        objs = []
        stack = []
        start_idx = None
        for idx, ch in enumerate(s):
            if ch in "{[":
                if not stack:
                    start_idx = idx
                stack.append(ch)
            elif ch in "}]" and stack:
                opening = stack.pop()
                if (opening == "{" and ch == "}") or (opening == "[" and ch == "]"):
                    if not stack and start_idx is not None:
                        objs.append(s[start_idx : idx + 1])
                        start_idx = None
                else:
                    # mismatched - reset
                    stack = []
                    start_idx = None
        return objs

    i = 1
    while i < len(sections):
        header = sections[i]
        content = sections[i + 1].strip() if i + 1 < len(sections) else ""

        # Capture tool request for path generation to extract keywords
        if "ToolCallRequestEvent" in header and "path_generation_agent" in header:
            # attempt to find a JSON object with keywords in the content
            json_match = re.search(r"(\{.*?\})", content, re.DOTALL)
            if json_match:
                try:
                    data = json.loads(json_match.group(0))
                    # keep only keyword_* entries, preserve numeric order
                    path_keywords = [v for k, v in sorted(data.items()) if k.startswith("keyword")]
                except json.JSONDecodeError:
                    path_keywords = None

        if "TextMessage" in header:
            match = re.search(r"TextMessage \((.*?)\)", header)
            if match:
                agent_name = match.group(1)
                formatted_name = agent_name.replace("_", " ").title()

                # Replace the first user heading with a more descriptive title
                if agent_name == "user" and not first_user_seen:
                    markdown_lines.append("## Initial research direction")
                    first_user_seen = True
                else:
                    markdown_lines.append(f"## {formatted_name}")

                # Special handling: keep JSON intact for coding-related agents (case-insensitive)
                agent_name_lower = agent_name.lower()
                if "coding" in agent_name_lower:
                    # extract all JSON-like objects (balanced {}) or arrays []
                    json_objs = _extract_json_objects(content)
                    remaining = content
                    if json_objs:
                        for obj in json_objs:
                            # remove the first occurrence of this obj from remaining
                            remaining = remaining.replace(obj, "", 1).strip()
                            try:
                                parsed = json.loads(obj)
                                pretty = json.dumps(parsed, indent=2)
                                markdown_lines.append("```json")
                                markdown_lines.append(pretty)
                                markdown_lines.append("```")
                            except json.JSONDecodeError:
                                # if it's not valid JSON, include the raw snippet in a generic code fence
                                markdown_lines.append("```")
                                markdown_lines.append(obj)
                                markdown_lines.append("```")
                        # if any non-json text remains, append it as normal content
                        if remaining:
                            markdown_lines.append(remaining)
                    else:
                        # no JSON found; include raw content
                        markdown_lines.append(content)

                # For scientist and novelty critic, try to unpack JSON into sections
                elif agent_name_lower in ["scientist", "novelty_critic_agent"]:
                    json_match = re.search(r"(\{.*\}|\[.*\])", content, re.DOTALL)
                    if json_match:
                        json_part = json_match.group(0)
                        other_text = content.replace(json_part, "").strip()

                        try:
                            data = json.loads(json_part)
                            for key, value in data.items():
                                markdown_lines.append(f"### {key.replace('_', ' ').title()}")
                                if isinstance(value, list):
                                    markdown_lines.append("\n".join(f"* {item}" for item in value))
                                else:
                                    markdown_lines.append(str(value))
                        except json.JSONDecodeError:
                            # if it's not valid json, just append the content as is
                            markdown_lines.append(content)

                        if other_text:
                            markdown_lines.append(other_text)
                    else:
                        markdown_lines.append(content)

                else:
                    markdown_lines.append(content)

        # Rename Summary sections and include extracted path keywords when available
        elif "Summary" in header:
            markdown_lines.append("## Generated path from knowledge graph")
            # If we captured keywords from the path generation request, list them
            if path_keywords:
                markdown_lines.append("### Used keywords")
                for kw in path_keywords:
                    markdown_lines.append(f"* {kw}")

            summary_points = [f"* {line.strip()}" for line in content.split("\n") if line.strip()]
            if summary_points:
                markdown_lines.append("\n".join(summary_points))

        i += 2

    return "\n\n".join(markdown_lines)


def main():
    """Main function to parse arguments and run the parser."""
    parser = argparse.ArgumentParser(description="Parse autogen log files to markdown.")
    parser.add_argument("input_file", type=Path, help="Path to the input log file.")
    parser.add_argument("output_file", type=Path, help="Path to the output markdown file.")
    args = parser.parse_args()

    if not args.input_file.is_file():
        print(f"Error: Input file not found at {args.input_file}")
        return

    log_content = args.input_file.read_text()
    markdown_content = parse_log_to_markdown(log_content)

    args.output_file.parent.mkdir(parents=True, exist_ok=True)
    args.output_file.write_text(markdown_content)
    print(f"Successfully converted {args.input_file} to {args.output_file}")


if __name__ == "__main__":
    main()
