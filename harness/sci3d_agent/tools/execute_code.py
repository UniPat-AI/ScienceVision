def run_execute_code(args: dict, kernel) -> dict:
    code = args.get("code", "")
    return kernel.execute(code)
