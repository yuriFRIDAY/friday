from core.tools.calculator import CalculatorTool


class ToolManager:

    def __init__(self):
        self.tools = {}

    def register(self, name, tool):
        self.tools[name] = tool

    def list_tools(self):
        return list(self.tools.keys())

    def get(self, name):
        return self.tools.get(name)

    def load_default_tools(self):

        calculator = CalculatorTool()

        self.register("calculator", calculator)