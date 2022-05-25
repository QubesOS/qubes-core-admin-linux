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
                                   'upgrading'}
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
        for arg, properties in AgentArgs.OPTIONS.items():
            parser.add_argument('--' + arg, **properties)
        verbosity = parser.add_mutually_exclusive_group()
        for arg, properties in AgentArgs.EXCLUSIVE_OPTIONS.items():
            verbosity.add_argument('--' + arg, **properties)

    @staticmethod
    def to_cli_args(args):
        args_dict = vars(args)

        cli_args = []
        for k in AgentArgs.ALL_OPTIONS.keys():
            if AgentArgs.ALL_OPTIONS[k]["action"] == "store_true":
                if args_dict[k.replace("-", "_")]:
                    cli_args.append("--" + k)
            else:
                cli_args.extend(("--" + k, args_dict[k.replace("-", "_")]))
        return cli_args
