class AgentArgs:
    # To avoid code repeating when we want to retrieve arguments
    OPTIONS = {
        "log": {"action": 'store',
                "default": "INFO",
                "help": 'Provide logging level. Values: DEBUG, INFO (default) '
                        'WARNING, ERROR, CRITICAL'},
        "no-refresh": {"action": 'store_true',
                       "help": 'Do not refresh available packages before '
                               'upgrading'},
        "force-upgrade": {"action": 'store_true',
                          "help": 'Try upgrade even if errors are '
                                  'encountered (like a refresh error)'},
        "leave-obsolete": {"action": 'store_true',
                           "help": 'Do not remove obsolete packages during '
                                   'upgrading'},
        "no-progress": {"action": "store_true",
                        "help": "Do not show upgrading progress."}
    }
    EXCLUSIVE_OPTIONS = {
        "show-output": {"action": 'store_true',
                        "help": 'Show output of management commands'},
        "quiet": {"action": 'store_true',
                  "help": 'Do not print anything to stdout'}
    }
    ALL_OPTIONS = {**OPTIONS, **EXCLUSIVE_OPTIONS}

    @staticmethod
    def add_arguments(parser):
        """
        Add common arguments to the parser.
        """
        for arg, properties in AgentArgs.OPTIONS.items():
            parser.add_argument('--' + arg, **properties)
        verbosity = parser.add_mutually_exclusive_group()
        for arg, properties in AgentArgs.EXCLUSIVE_OPTIONS.items():
            verbosity.add_argument('--' + arg, **properties)

    @staticmethod
    def to_cli_args(args):
        """
        Parse selected args values to flags ready to pass
        to an agent entrypoint.
        """
        args_dict = vars(args)

        cli_args = []
        for key, value in AgentArgs.ALL_OPTIONS.items():
            if value["action"] == "store_true":
                if args_dict[key.replace("-", "_")]:
                    cli_args.append("--" + key)
            else:
                cli_args.extend(("--" + key, args_dict[key.replace("-", "_")]))
        return cli_args
