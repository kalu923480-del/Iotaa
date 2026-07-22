import subprocess, sys

result = subprocess.run(
    ["python", "-m", "compileall", "-q",
     "handlers/economy.py", "handlers/ai_chat.py",
     "handlers/progress.py", "utils/xp.py", "bot.py"],
    capture_output=True, text=True
)
print("compileall stdout:", result.stdout)
print("compileall stderr:", result.stderr)
print("compileall rc:", result.returncode)

try:
    import ast
    ast.parse(open("bot.py").read())
    print("bot.py AST parse: OK")
except SyntaxError as e:
    print("bot.py AST parse FAILED:", e)
