class FridayContext:

    def __init__(self):
        self.name = "FRIDAY"
        self.role = "personal_assistant"
        self.language = "pt-BR"
        self.owner = "owner"

    def get_context(self):

        return {
            "name": self.name,
            "role": self.role,
            "language": self.language,
            "owner": self.owner
        }