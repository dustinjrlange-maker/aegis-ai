"""
Protocol Registry — Aegis AI
Manages registration, ordering, and routing through all active protocols.
"""


class ProtocolRegistry:
    """Central registry for all Aegis protocols."""

    def __init__(self):
        self._protocols = {}  # name -> Protocol instance
        self._order = []      # sorted list of protocol names by priority (highest first)

    def register(self, protocol):
        """Register a protocol with the registry.

        Args:
            protocol: A Protocol instance to register.
        """
        self._protocols[protocol.name] = protocol
        if not protocol._initialized:
            protocol.initialize()
        self._rebuild_order()

    def unregister(self, name):
        """Remove a protocol from the registry."""
        if name in self._protocols:
            del self._protocols[name]
            self._rebuild_order()

    def get(self, name):
        """Get a protocol by name."""
        return self._protocols.get(name)

    def _rebuild_order(self):
        """Rebuild the priority-sorted protocol order."""
        self._order = sorted(
            self._protocols.keys(),
            key=lambda n: self._protocols[n].priority,
            reverse=True  # Highest priority first
        )

    def process_input(self, user_input, context):
        """Run user input through all active protocols in priority order.

        Returns:
            Dict with final processed input, any context injections,
            and whether a protocol intercepted the response.
        """
        result = {
            "input": user_input,
            "context_injections": [],
            "full_context_injections": [],
            "intercept": False,
            "response": "",
        }

        for name in self._order:
            protocol = self._protocols[name]
            if not protocol.enabled:
                continue

            try:
                proto_result = protocol.process_input(result["input"], context)

                if proto_result.get("input"):
                    result["input"] = proto_result["input"]

                if proto_result.get("context_injection"):
                    result["context_injections"].append(proto_result["context_injection"])

                if proto_result.get("full_context_injection"):
                    result["full_context_injections"].append(proto_result["full_context_injection"])

                # If a protocol intercepts, it handles the response directly
                if proto_result.get("intercept"):
                    result["intercept"] = True
                    result["response"] = proto_result.get("response", "")
                    result["intercepted_by"] = name
                    break

            except Exception as e:
                print(f"  [Protocol '{name}' error on input: {e}]")

        return result

    def process_output(self, response, context):
        """Run the agent's response through all active protocols in priority order.

        Returns:
            Dict with final processed response.
        """
        result = {
            "response": response,
            "suppress": False,
            "appended": [],
        }

        for name in self._order:
            protocol = self._protocols[name]
            if not protocol.enabled:
                continue

            try:
                proto_result = protocol.process_output(result["response"], context)

                if proto_result.get("response"):
                    result["response"] = proto_result["response"]

                if proto_result.get("suppress"):
                    result["suppress"] = True
                    break

                if proto_result.get("append"):
                    result["appended"].append(proto_result["append"])

            except Exception as e:
                print(f"  [Protocol '{name}' error on output: {e}]")

        # Append any protocol additions
        if result["appended"] and not result["suppress"]:
            result["response"] = result["response"] + "\n" + "\n".join(result["appended"])

        return result

    def handle_command(self, command, args=""):
        """Route a slash command to the appropriate protocol.

        Args:
            command: The command string (e.g., "wellness")
            args: Arguments after the command

        Returns:
            (handled: bool, response: str)
        """
        for name in self._order:
            protocol = self._protocols[name]
            if not protocol.enabled:
                continue

            for cmd_info in protocol.get_commands():
                if cmd_info["command"] == command:
                    handler = getattr(protocol, cmd_info["handler"])
                    return True, handler(args)

        return False, ""

    def get_all_status(self):
        """Get status of all registered protocols."""
        statuses = []
        for name in self._order:
            statuses.append(self._protocols[name].get_status())
        return statuses

    def enable(self, name):
        """Enable a protocol by name."""
        if name in self._protocols:
            self._protocols[name].enable()
            return True
        return False

    def disable(self, name):
        """Disable a protocol by name."""
        if name in self._protocols:
            self._protocols[name].disable()
            return True
        return False

    def list_protocols(self):
        """List all registered protocol names."""
        return list(self._order)
