"""OpenAI Responses API transport.

Some gateways serve a model only on ``/responses`` and answer HTTP 500 on
``/chat/completions`` for it — OpenCode Zen's ``muse-spark-1.3-contributor-free``
is the case that motivated this module. The request parameters are otherwise
identical to chat completions, so the builder is shared and only the dialect
flag differs.
"""

from .openai_compatible import OpenAICompatibleTransport


class OpenAIResponsesTransport(OpenAICompatibleTransport):
    """ChatOpenAI configured to post to the Responses API."""

    use_responses_api = True
