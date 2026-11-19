class AppService:
    """Root application service matching NestJS AppService."""

    def get_hello(self) -> str:
        return "Hello World!"

    def getHello(self) -> str:
        return self.get_hello()


__all__ = ["AppService"]
