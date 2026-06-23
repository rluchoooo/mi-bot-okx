import ast, glob
for file in ["scanner.py", "risk.py", "risk_manager.py", "order_execution_engine.py"]:
    with open(file, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            print(f"{file}:{node.lineno}")
