import json
import logging
from typing import List, Dict, Any, Optional

from haystack import component
from haystack.dataclasses import ChatMessage, ChatRole
from haystack.lazy_imports import LazyImport

logger = logging.getLogger(__name__)

with LazyImport("Run 'pip install openapi3'") as openapi_imports:
    from openapi3 import OpenAPI


@component
class OpenAPIServiceConnector:
    """
    OpenAPIServiceConnector connects to OpenAPI services, allowing for the invocation of methods specified in
    an OpenAPI specification of that service. It integrates with ChatMessage interface, where messages are used to
    determine the method to be called and the parameters to be passed. The message payload should be a JSON formatted
    string consisting of the method name and the parameters to be passed to the method. The method name and parameters
    are then used to invoke the method on the OpenAPI service. The response from the service is returned as a
    ChatMessage.

    Before using this component, one needs to register functions from the OpenAPI specification with LLM.
    This can be done using the OpenAPIServiceToFunctions component.
    """

    def __init__(self, service_auths: Optional[Dict[str, Any]] = None):
        """
        Initializes the OpenAPIServiceConnector instance
        :param service_auths: A dictionary containing the service name and token to be used for authentication.
        """
        openapi_imports.check()
        self.service_authentications = service_auths or {}

    @component.output_types(service_response=Dict[str, Any])
    def run(self, messages: List[ChatMessage], service_openapi_spec: Dict[str, Any]) -> Dict[str, List[ChatMessage]]:
        """
        Processes a list of chat messages to invoke a method on an OpenAPI service. It parses the last message in the
        list, expecting it to contain an OpenAI function calling descriptor (name & parameters) in JSON format.

        :param messages: A list of `ChatMessage` objects representing the chat history.
        :type messages: List[ChatMessage]
        :param service_openapi_spec: The OpenAPI JSON specification object of the service.
        :type service_openapi_spec: JSON object
        :return: A dictionary with a key `"service_response"`, containing the response from the OpenAPI service.
        :rtype: Dict[str, List[ChatMessage]]
        :raises ValueError: If the last message is not from the assistant or if it does not contain the correct payload
        to invoke a method on the service.
        """

        last_message = messages[-1]
        if not last_message.is_from(ChatRole.ASSISTANT):
            raise ValueError(f"{last_message} is not from the assistant.")

        method_invocation_descriptor = self._parse_message(last_message.content)

        # instantiate the OpenAPI service for the given specification
        openapi_service = OpenAPI(service_openapi_spec)
        self._authenticate_service(openapi_service)

        service_response = self._invoke_method(openapi_service, method_invocation_descriptor)
        return {"service_response": [ChatMessage.from_user(str(service_response))]}

    def _parse_message(self, content: str) -> Dict[str, Any]:
        """
        Parses the message content to extract the method invocation descriptor.

        :param content: The JSON string content of the message.
        :type content: str
        :return: A dictionary with method name and arguments.
        :rtype: Dict[str, Any]
        :raises ValueError: If the content is not valid JSON or lacks required fields.
        """
        try:
            method_invocation_descriptor = json.loads(content)
        except json.JSONDecodeError:
            raise ValueError("Invalid JSON content, cannot parse invocation message.", content)

        if "name" not in method_invocation_descriptor or "arguments" not in method_invocation_descriptor:
            raise ValueError("Missing required fields in the invocation message content.", content)

        method_invocation_descriptor["arguments"] = json.loads(method_invocation_descriptor["arguments"])
        return method_invocation_descriptor

    def _authenticate_service(self, openapi_service: OpenAPI):
        """
        Authenticates with the OpenAPI service if required.

        :param openapi_service: The OpenAPI service instance.
        :type openapi_service: OpenAPI
        :raises ValueError: If authentication fails or is not found.
        """
        if openapi_service.components.securitySchemes:
            auth_method = list(openapi_service.components.securitySchemes.keys())[0]
            service_title = openapi_service.info.title
            if service_title not in self.service_authentications:
                raise ValueError(f"Service {service_title} not found in service_authentications.")
            openapi_service.authenticate(auth_method, self.service_authentications[service_title])

    def _invoke_method(self, openapi_service: OpenAPI, method_invocation_descriptor: Dict[str, Any]) -> Any:
        """
        Invokes the specified method on the OpenAPI service.

        :param openapi_service: The OpenAPI service instance.
        :type openapi_service: OpenAPI
        :param method_invocation_descriptor: The method name and arguments.
        :type method_invocation_descriptor: Dict[str, Any]
        :return: A service JSON response.
        :rtype: Any
        :raises RuntimeError: If the method is not found or invocation fails.
        """
        name = method_invocation_descriptor["name"]
        # a bit convoluted, but we need to pass parameters, data, or both to the method
        # depending on the openapi operation specification, can't use None as a default value
        method_call_params = {}
        if (parameters := method_invocation_descriptor["arguments"].get("parameters")) is not None:
            method_call_params["parameters"] = parameters
        if (arguments := method_invocation_descriptor["arguments"].get("requestBody")) is not None:
            method_call_params["data"] = arguments

        method_to_call = getattr(openapi_service, f"call_{name}", None)
        if not callable(method_to_call):
            raise RuntimeError(f"Operation {name} not found in OpenAPI specification {openapi_service.info.title}")

        # this will call the underlying service REST API
        return method_to_call(**method_call_params)
