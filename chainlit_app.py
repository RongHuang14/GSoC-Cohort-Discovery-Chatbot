import chainlit as cl
from langchain_openai import ChatOpenAI
import os
import json
import uuid
import traceback
import time  # Ensure time module is correctly imported
from datetime import datetime  # Backup time module
from dotenv import load_dotenv

# Import custom modules
from schema_parser import parse_pcdc_schema, extract_relevant_schema, standardize_terms
from query_builder import analyze_query_complexity, decompose_query, combine_results
from context_manager import session_manager
from prompt_builder import create_enhanced_prompt, create_nested_query_prompt
# Smart import for ChromaDB - handles SQLite version and other issues
from ChromaDB import ChromaDBManager
from chromadb_history_reader import ChromaDBHistoryReader

load_dotenv()

# Create LLM instance with retry mechanism
llm = ChatOpenAI(
    model="gpt-3.5-turbo",
    temperature=0,
    api_key=os.getenv("OPENAI_API_KEY"),
    max_retries=3,  # Add retry mechanism
    request_timeout=60  # Increase timeout
)

# Load PCDC schema
node_properties = {}
term_mappings = {}

try:
    node_properties, term_mappings = parse_pcdc_schema("pcdc-schema-prod-20250114.json")
    print(f"Successfully loaded PCDC schema, node count: {len(node_properties)}")
except Exception as e:
    print(f"Failed to load PCDC schema: {str(e)}")

# Global session storage (simulates database)
session_list = {}

# Initialize ChromaDB manager and history reader
chroma_manager = ChromaDBManager()
history_reader = ChromaDBHistoryReader()

# Authentication callback for Chainlit
@cl.password_auth_callback
def auth_callback(username: str, password: str):
    """Simple password authentication callback"""
    # For development/demo purposes - in production, use proper authentication
    valid_users = {
        "admin": "admin123",
        "user": "password",
        "demo": "demo123",
        "guest": "guest"
    }
    
    if username in valid_users and valid_users[username] == password:
        return cl.User(
            identifier=username,
            metadata={
                "role": "admin" if username == "admin" else "user",
                "joined": datetime.now().isoformat()
            }
        )
    return None

# Alternative: Header-based authentication (uncomment if needed)
# @cl.header_auth_callback
# def auth_header_callback(headers: dict):
#     """Header-based authentication callback"""
#     user_id = headers.get("x-user-id", "anonymous")
#     
#     if user_id:
#         return cl.User(
#             identifier=user_id,
#             metadata={
#                 "role": "user",
#                 "joined": datetime.now().isoformat()
#             }
#         )
#     return None

def save_to_chat_history(user_query, llm_response, session_id):
    """Save LLM response to chat_history directory"""
    try:
        # Get current user for file naming
        current_user = cl.user_session.get("user")
        user_id = current_user.identifier if current_user else "anonymous"
        
        # Create chat_history directory if it doesn't exist
        chat_history_dir = "chat_history"
        if not os.path.exists(chat_history_dir):
            os.makedirs(chat_history_dir)
        
        # Generate filename with timestamp and user
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"{timestamp}_{user_id}_{session_id[:8]}.txt"
        filepath = os.path.join(chat_history_dir, filename)
        
        # Extract query and explanation from response
        response_content = ""
        if isinstance(llm_response, dict):
            if llm_response.get('query'):
                response_content += f"Query: {llm_response.get('query')}\n"
            if llm_response.get('variables') and llm_response.get('variables') != "{}":
                response_content += f"Variables: {llm_response.get('variables')}\n"
            if llm_response.get('explanation'):
                response_content += f"Explanation: {llm_response.get('explanation')}\n"
        else:
            response_content = str(llm_response)
        
        # Save to file
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(f"User: {user_id}\n")
            f.write(f"User Query: {user_query}\n")
            f.write(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Session ID: {session_id}\n")
            f.write("=" * 50 + "\n")
            f.write(response_content)
        
        print(f"Response saved to: {filepath}")
        return filepath
        
    except Exception as e:
        print(f"Error saving to chat_history: {str(e)}")
        traceback.print_exc()
        return None

@cl.step
async def show_history_sidebar():
    """Create and populate the history sidebar"""
    try:
        # Get current user session info
        current_user = cl.user_session.get("user")
        user_id = current_user.identifier if current_user else "anonymous"
        
        # Get recent chat history for current user
        history_data = history_reader.get_recent_history(limit=10)
        
        if not history_data:
            return f"No chat history found for user: {user_id}"
        
        # Create sidebar content with clickable history items
        sidebar_content = f"## 📋 Chat History for {user_id}\n\n"
        
        for i, msg in enumerate(history_data):
            timestamp = msg.get('timestamp', '')
            user_query = msg.get('user_query', 'Unknown query')
            
            # Format timestamp
            try:
                dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                formatted_time = dt.strftime('%m-%d %H:%M')
            except:
                formatted_time = timestamp[:16] if timestamp else "Unknown"
            
            # Truncate long queries for display
            display_query = user_query[:50] + "..." if len(user_query) > 50 else user_query
            
            # Create clickable history item with action
            sidebar_content += f"### 🕒 {formatted_time}\n"
            sidebar_content += f"**Query:** {display_query}\n"
            sidebar_content += f"**Session:** {msg.get('session_id', 'Unknown')[:8]}...\n"
            sidebar_content += f"```\nhistory_item_{i}\n```\n\n"
        
        return sidebar_content
        
    except Exception as e:
        return f"Error loading history: {str(e)}"

@cl.action_callback("show_history")
async def show_history_action(action):
    """Handle showing chat history in sidebar"""
    try:
        # Create sidebar step
        async with cl.Step(name="Chat History", type="tool") as step:
            history_content = await show_history_sidebar()
            step.output = history_content
        
        # Also send as a message
        await cl.Message(content="📋 **Chat history displayed in sidebar above.**").send()
        
    except Exception as e:
        await cl.Message(content=f"Error showing history: {str(e)}").send()

@cl.action_callback("search_history")
async def search_history_action(action):
    """Handle searching chat history"""
    try:
        # Ask for search term
        res = await cl.AskUserMessage(content="🔍 Enter search term:", timeout=30).send()
        
        if res and res['output']:
            search_term = res['output']
            
            # Search in history
            search_results = history_reader.search_history(search_term, limit=5)
            
            if search_results:
                async with cl.Step(name=f"Search Results for '{search_term}'", type="tool") as step:
                    content = f"## 🔍 Search Results for '{search_term}'\n\n"
                    for i, msg in enumerate(search_results):
                        content += format_history_message_for_sidebar(msg, i+1)
                    step.output = content
                
                await cl.Message(content=f"🔍 Found {len(search_results)} results for '{search_term}'").send()
            else:
                await cl.Message(content=f"🔍 No results found for '{search_term}'").send()
        
    except Exception as e:
        await cl.Message(content=f"Error searching: {str(e)}").send()

@cl.action_callback("view_sessions")
async def view_sessions_action(action):
    """Handle viewing all sessions"""
    try:
        sessions = history_reader.get_all_sessions()
        
        if sessions:
            async with cl.Step(name="All Chat Sessions", type="tool") as step:
                content = "## 📂 All Chat Sessions\n\n"
                for i, session in enumerate(sessions[:10]):
                    content += f"### Session {i+1}\n"
                    content += f"**ID:** {session['display_name']}\n"
                    content += f"**Last Activity:** {session.get('last_message', 'Unknown')}\n\n"
                step.output = content
            
            await cl.Message(content=f"📂 Found {len(sessions)} chat sessions").send()
        else:
            await cl.Message(content="📂 No chat sessions found").send()
            
    except Exception as e:
        await cl.Message(content=f"Error loading sessions: {str(e)}").send()

def format_history_message_for_sidebar(msg_data, index):
    """Format a history message for sidebar display"""
    try:
        timestamp = msg_data.get('timestamp', '')
        if timestamp:
            try:
                dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                formatted_time = dt.strftime('%Y-%m-%d %H:%M:%S')
            except:
                formatted_time = timestamp
        else:
            formatted_time = "Unknown time"
        
        content = f"### Result {index}\n"
        content += f"**📅 Time:** {formatted_time}\n\n"
        
        if msg_data.get('user_query'):
            content += f"**🙋 Query:** {msg_data['user_query']}\n\n"
        
        if msg_data.get('graphql_query'):
            # Truncate long queries for sidebar
            query = msg_data['graphql_query']
            if len(query) > 200:
                query = query[:200] + "..."
            content += f"**📊 GraphQL:**\n```graphql\n{query}\n```\n\n"
        
        if msg_data.get('explanation'):
            explanation = msg_data['explanation']
            if len(explanation) > 150:
                explanation = explanation[:150] + "..."
            content += f"**💡 Explanation:** {explanation}\n\n"
        
        content += "---\n\n"
        return content
        
    except Exception as e:
        return f"Error formatting message: {str(e)}\n\n"

@cl.on_chat_start
async def on_chat_start():
    try:
        # Get current user info (for authentication support)
        current_user = cl.user_session.get("user")
        user_id = current_user.identifier if current_user else f"anonymous_{uuid.uuid4().hex[:8]}"
        
        # Get thread_id for persistence
        thread_id = cl.user_session.get("id")
        
        # Create new session with user context
        session_id = str(uuid.uuid4())
        memory = session_manager.get_or_create_session(session_id)
        
        # Store session ID and user context
        cl.user_session.set("session_id", session_id)
        cl.user_session.set("user_id", user_id)
        
        # Generate session name
        chat_name = f"Session {datetime.now().strftime('%m-%d %H:%M')}"
        
        # Store system info with user context
        cl.user_session.set("system_info", {
            "model": "gpt-3.5-turbo",
            "purpose": "GraphQL Query Generator",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "user_id": user_id,
            "thread_id": thread_id
        })
        
        # Record session in global storage with user context
        global session_list
        session_list[session_id] = {
            "name": chat_name,
            "thread_id": thread_id,
            "user_id": user_id,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "messages": []
        }
        
        # Set header actions in the top navigation bar
        await cl.set_starters([
            cl.Starter(
                label="📋 Chat History",
                message="show_history_action",
                icon="/public/history.svg"
            ),
            cl.Starter(
                label="🔍 Search History", 
                message="search_history_action",
                icon="/public/search.svg"
            ),
            cl.Starter(
                label="📂 View Sessions",
                message="view_sessions_action", 
                icon="/public/sessions.svg"
            )
        ])
        
        # Use Chainlit to set session name
        try:
            await cl.ChatSettings(name=chat_name).send()
        except Exception as e:
            print(f"Unable to set session name: {str(e)}")
        
        # Send welcome message with user context
        welcome_msg = f"""🎉 **Welcome to the PCDC GraphQL Query Generator!**

👤 **User:** {user_id}
🔗 **Session:** {session_id[:8]}...

📝 **How to use:**
- Enter your natural language query to generate GraphQL queries
- Ask questions about PCDC data schema and get structured queries
- Use the starter buttons above for chat history features

💡 **Examples:**
- "Get all users with age greater than 18"
- "Find patients by diagnosis"
- "Show me all available data fields"

Please enter your query below! 👇"""
        
        # Send welcome message without actions
        await cl.Message(content=welcome_msg).send()
        
    except Exception as e:
        print(f"Error in on_chat_start: {str(e)}")
        traceback.print_exc()

def is_system_query(query):
    """Check if query is about system, model or identity"""
    system_keywords = [
        "你是谁", "你是什么", "你叫什么", "你的名字", "什么模型", 
        "什么助手", "哪个模型", "什么系统", "什么版本",
        "who are you", "what are you", "your name", "which model",
        "what model", "what system", "what version"
    ]
    
    query = query.lower()
    return any(keyword in query for keyword in system_keywords)

def is_history_query(query):
    """Check if query is asking for chat history"""
    history_keywords = [
        "历史", "记录", "history", "previous", "past", "earlier",
        "会话", "session", "chat history", "对话记录",
        "之前的", "earlier conversations", "show history"
    ]
    
    query = query.lower()
    return any(keyword in query for keyword in history_keywords)

def format_history_message(msg_data):
    """Format a history message for display"""
    try:
        timestamp = msg_data.get('timestamp', '')
        if timestamp:
            # Parse and format timestamp
            try:
                dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                formatted_time = dt.strftime('%Y-%m-%d %H:%M:%S')
            except:
                formatted_time = timestamp
        else:
            formatted_time = "Unknown time"
        
        content = f"**📅 {formatted_time}**\n\n"
        
        if msg_data.get('user_query'):
            content += f"**🙋 User Query:**\n{msg_data['user_query']}\n\n"
        
        if msg_data.get('graphql_query'):
            content += f"**📊 GraphQL Query:**\n```graphql\n{msg_data['graphql_query']}\n```\n\n"
        
        if msg_data.get('variables') and msg_data['variables'] != '{}':
            content += f"**🔧 Variables:**\n```json\n{msg_data['variables']}\n```\n\n"
        
        if msg_data.get('explanation'):
            content += f"**💡 Explanation:**\n{msg_data['explanation']}\n\n"
        
        content += f"**🆔 Session:** {msg_data.get('session_id', 'Unknown')[:8]}..."
        
        return content
    except Exception as e:
        return f"Error formatting message: {str(e)}"

def update_chat_name(session_id, query):
    """Update session name based on user query"""
    # Take first 20 characters of query as session name
    chat_name = query[:20] + ("..." if len(query) > 20 else "")
    # Update global session list
    global session_list
    if session_id in session_list:
        session_list[session_id]["name"] = chat_name
    return chat_name

# Record message history
def record_message(session_id, role, content):
    global session_list
    if session_id in session_list:
        session_list[session_id]["messages"].append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })

# Enhanced session resume hook following official standards
@cl.on_chat_resume
async def on_chat_resume(thread: dict):
    """Called when user resumes a persisted conversation"""
    try:
        thread_id = thread.get("id") if isinstance(thread, dict) else str(thread)
        print(f"Resuming thread: {thread_id}")
        
        # Get current user context
        current_user = cl.user_session.get("user")
        user_id = current_user.identifier if current_user else "anonymous"
        
        # Find corresponding session in ChromaDB or global storage
        session_found = False
        session_data = None
        
        # Try to find session in global storage first
        for session_id, data in session_list.items():
            if (data.get("thread_id") == thread_id or 
                data.get("user_id") == user_id):
                session_data = data
                session_found = True
                cl.user_session.set("session_id", session_id)
                break
        
        # If not found in memory, try to retrieve from ChromaDB
        if not session_found:
            try:
                # Get session history from ChromaDB
                history_data = history_reader.get_recent_history(limit=50)
                if history_data:
                    # Create a new session with historical context
                    session_id = str(uuid.uuid4())
                    memory = session_manager.get_or_create_session(session_id)
                    
                    # Restore conversation history to memory
                    for msg in history_data:
                        if msg.get('user_query'):
                            memory.add_message({"role": "user", "content": msg['user_query']})
                        if msg.get('explanation'):
                            memory.add_message({"role": "assistant", "content": msg['explanation']})
                    
                    cl.user_session.set("session_id", session_id)
                    session_found = True
                    print(f"Restored session from ChromaDB with {len(history_data)} messages")
            except Exception as e:
                print(f"Error restoring from ChromaDB: {str(e)}")
        
        if session_found:
            # Set session name from thread metadata or create new one
            thread_name = thread.get('name') if isinstance(thread, dict) else None
            chat_name = thread_name or session_data.get("name", "Resumed Session")
            
            try:
                await cl.ChatSettings(name=chat_name).send()
            except Exception as e:
                print(f"Unable to set resumed session name: {str(e)}")
            
            # Send resume confirmation with context
            resume_msg = f"""🔄 **Session Resumed**

👤 **User:** {user_id}
🆔 **Thread ID:** {thread_id}
📅 **Resumed at:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

Your conversation history has been restored. Continue where you left off!"""
            
            await cl.Message(content=resume_msg, author="System").send()
            
            # Automatically show history sidebar
            await show_history_action(None)
        else:
            # No session found, create new one
            print(f"No session found for thread {thread_id}, creating new session")
            await cl.Message(content="⚠️ Could not restore previous session. Starting fresh!", author="System").send()
    
    except Exception as e:
        print(f"Error in on_chat_resume: {str(e)}")
        traceback.print_exc()
        await cl.Message(content="❌ Error resuming session. Starting fresh!", author="System").send()

@cl.on_message
async def main(message: cl.Message):
    # Get session ID and memory
    session_id = cl.user_session.get("session_id")
    user_id = cl.user_session.get("user_id", "anonymous")
    memory = session_manager.get_or_create_session(session_id)
    
    # Record user message with user context
    record_message(session_id, "user", message.content)
    
    # Update session name (only on first message)
    if session_id in session_list and len(session_list[session_id]["messages"]) <= 1:
        chat_name = update_chat_name(session_id, message.content)
        
        # Try to update session name
        try:
            await cl.ChatSettings(name=chat_name).send()
        except Exception as e:
            print(f"Unable to update session name: {str(e)}")
    
    # Handle special starter messages for history features
    if message.content == "show_history_action":
        await show_history_action(None)
        return
    elif message.content == "search_history_action":
        await search_history_action(None)
        return
    elif message.content == "view_sessions_action":
        await view_sessions_action(None)
        return
    
    # Check if this is a system info query
    if is_system_query(message.content):
        response = f"我是基于GPT-3.5-turbo的AI助手，专门用于生成PCDC GraphQL查询。当前用户: {user_id}，会话: {session_id[:8]}..."
        await cl.Message(content=response).send()
        record_message(session_id, "assistant", response)
        
        # Save system response to chat history with user context
        save_to_chat_history(message.content, {"explanation": response}, session_id)
        
        # Save to ChromaDB with user context
        chroma_manager.store_response(message.content, {"explanation": response}, session_id)
        return
    
    # Check if this is a history query and handle with sidebar
    if is_history_query(message.content):
        await show_history_action(None)
        await cl.Message(content="📋 Chat history displayed in the sidebar above. Use the starter buttons for more options.").send()
        return
    
    # Send thinking message
    thinking_msg = cl.Message(content="Generating query...")
    await thinking_msg.send()
    
    try:
        # Process user query
        standardized_query = standardize_terms(message.content, term_mappings)
        complexity = analyze_query_complexity(standardized_query)
        relevant_schema = extract_relevant_schema(standardized_query, node_properties)
        
        result = None
        
        if complexity == "complex":
            # Handle complex query
            thinking_msg.content = "This is a complex query, breaking it down..."
            await thinking_msg.update()
            
            sub_queries = decompose_query(standardized_query)
            sub_results = []
            
            for i, sub_query in enumerate(sub_queries):
                thinking_msg.content = f"Processing sub-query {i+1}/{len(sub_queries)}: {sub_query}"
                await thinking_msg.update()
                
                sub_schema = extract_relevant_schema(sub_query, node_properties)
                conversation_history = memory.get_formatted_context()
                prompt_text = create_enhanced_prompt(sub_query, sub_schema, conversation_history)
                
                response = llm.invoke(prompt_text)
                
                try:
                    sub_result = json.loads(response.content)
                    sub_results.append(sub_result)
                    
                    memory.add_message({"role": "user", "content": sub_query})
                    memory.add_message({"role": "assistant", "content": response.content})
                    
                    # Save sub-query result to chat history
                    save_to_chat_history(sub_query, sub_result, session_id)
                    
                    # Save sub-query result to ChromaDB
                    chroma_manager.store_response(sub_query, sub_result, session_id)
                except Exception as e:
                    print(f"Failed to parse sub-query result: {str(e)}")
            
            result = combine_results(sub_results, standardized_query)
        else:
            # Handle simple query
            thinking_msg.content = "Generating GraphQL query..."
            await thinking_msg.update()
            
            conversation_history = memory.get_formatted_context()
            prompt_text = create_enhanced_prompt(standardized_query, relevant_schema, conversation_history)
            
            response = llm.invoke(prompt_text)
            print(f"LLM response: {response.content}")
            
            try:
                result = json.loads(response.content)
                print(f"Successfully parsed JSON: {result}")
            except Exception as e:
                print(f"Failed to parse JSON: {str(e)}")
                content = response.content
                import re
                query_match = re.search(r'```graphql\s*(.*?)\s*```', content, re.DOTALL)
                variables_match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
                
                query = query_match.group(1) if query_match else ""
                variables = variables_match.group(1) if variables_match else "{}"
                
                result = {
                    "query": query,
                    "variables": variables,
                    "explanation": "Query and variables extracted from response"
                }
            
            memory.add_message({"role": "user", "content": message.content})
            memory.add_message({"role": "assistant", "content": json.dumps(result)})
        
        # Format results
        response_parts = []
        
        # Add GraphQL query
        if result.get('query'):
            response_parts.append(f"**GraphQL Query:**\n```graphql\n{result.get('query', '')}\n```")
        
        # Add variables
        if result.get('variables'):
            try:
                variables_value = result.get('variables', '')
                if isinstance(variables_value, dict):
                    variables_value = json.dumps(variables_value, indent=2)
                
                response_parts.append(f"**Variables:**\n```json\n{variables_value}\n```")
            except Exception as e:
                print(f"Error formatting variables: {str(e)}")
        
        # Add explanation
        if result.get('explanation'):
            response_parts.append(f"**Explanation:**\n{result.get('explanation', '')}")
        
        # Join final response
        response_content = "\n\n".join(response_parts)
        
        # Update thinking message with result (without actions)
        thinking_msg.content = response_content
        await thinking_msg.update()
        
        # Record assistant response
        record_message(session_id, "assistant", response_content)
        
        # Save to chat history with user context
        save_to_chat_history(message.content, result, session_id)
        
        # Save to ChromaDB with user context
        chroma_manager.store_response(message.content, result, session_id)
        
    except Exception as e:
        error_msg = f"Error generating query: {str(e)}"
        print(error_msg)
        traceback.print_exc()
        thinking_msg.content = error_msg
        await thinking_msg.update()