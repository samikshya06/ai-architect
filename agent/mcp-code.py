import asyncio
import json
from openai import OpenAI
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent.parent / ".env")
# Initialize your LLM Client
# Ensure your OPENAI_API_KEY environment variable is set
openai_client = OpenAI()

# 1. Define the AWS Documentation MCP server parameters
server_params = StdioServerParameters(
    command="uvx", # You can also use "npx" if running the JS version
    args=["awslabs.aws-documentation-mcp-server"]
)

def convert_mcp_to_openai_tool(mcp_tool):
    """Converts an MCP tool definition into OpenAI function calling format."""
    return {
        "type": "function",
        "function": {
            "name": mcp_tool.name,
            "description": mcp_tool.description,
            "parameters": mcp_tool.input_schema
        }
    }

async def run_agentic_loop():
    print("🤖 Initializing AWS Documentation Agent...")
    
    # 2. Start the MCP server sub-process
    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            
            # Fetch tools exposed by the MCP server
            mcp_tools_resp = await session.list_tools()
            mcp_tools = {t.name: t for t in mcp_tools_resp.tools}
            
            # Convert tools for the OpenAI API
            openai_tools = [convert_mcp_to_openai_tool(t) for t in mcp_tools_resp.tools]

            # 3. Define the conversation history with the agent's system prompt
            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are an expert AWS Solutions Architect Agent. Your job is to recommend "
                        "the top AWS services for a given use case. You MUST search the official AWS "
                        "documentation using the provided tools to ensure your facts are accurate, up-to-date, "
                        "and structurally sound. Provide a final prioritized list of the top services with justifications."
                    )
                },
                {
                    "role": "user",
                    "content": "I need to build a highly scalable real-time chat application with millisecond latency. What are the top AWS services I should use?"
                }
            ]

            print(f"User Query: {messages[1]['content']}\n")
            
            # 4. First LLM Call: Give it the tools and the query
            response = openai_client.chat.completions.create(
                model="gpt-4o", # Or use gpt-4-turbo / gpt-3.5-turbo
                messages=messages,
                tools=openai_tools,
                tool_choice="auto"
            )
            
            response_message = response.choices[0].message
            messages.append(response_message)

            # 5. Check if the LLM decided it needs to search/read the docs
            if response_message.tool_calls:
                for tool_call in response_message.tool_calls:
                    tool_name = tool_call.function.name
                    tool_args = json.loads(tool_call.function.arguments)
                    
                    print(f"🔍 Agent is executing tool '{tool_name}' with args: {tool_args}...")
                    
                    # Execute the tool call via the MCP Server connection
                    mcp_result = await session.call_tool(name=tool_name, arguments=tool_args)
                    
                    # Convert MCP text response content to pass back to the LLM
                    tool_output = "".join([c.text for c in mcp_result.content if hasattr(c, 'text')])
                    
                    print(f"📄 Documentation fetched successfully! (Character count: {len(tool_output)})")
                    
                    # Append the tool result back to the conversation
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": tool_name,
                        "content": tool_output
                    })

                # 6. Second LLM Call: Give the model the context it retrieved to compile the final answer
                print("\n🧠 Agent is analyzing documentation data to formulate final response...")
                final_response = openai_client.chat.completions.create(
                    model="gpt-4o",
                    messages=messages
                )
                
                print("\n=== Agent's Recommendation ===")
                print(final_response.choices[0].message.content)
            else:
                # Fallback if the LLM answered directly without using the tool
                print("\n=== Agent's Recommendation (Direct Answer) ===")
                print(response_message.content)

if __name__ == "__main__":
    asyncio.run(run_agentic_loop())
