const API_BASE_URL = "http://localhost:8000";
let isSending = false;

function appendMessage(role, text) {
    const chatWindow = document.getElementById("chat-window");
    if (!chatWindow) {
        return;
    }

    const emptyState = document.getElementById("emptyState");
    if (emptyState) {
        emptyState.style.display = "none";
    }

    const messageElement = document.createElement("div");
    messageElement.className = "message " + role;

    text
        .trim()
        .split(/\n+/)
        .filter(Boolean)
        .forEach(function(paragraph) {
            const paragraphElement = document.createElement("p");
            paragraphElement.textContent = paragraph;
            messageElement.appendChild(paragraphElement);
        });

    chatWindow.appendChild(messageElement);
    messageElement.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function sendMessage() {
    if (isSending) {
        return;
    }

    const inputElement = document.getElementById("user-input");
    const userMessage = inputElement.value.trim();
    const sendButton = document.querySelector('button[onclick="sendMessage()"]');

    if (userMessage === "") {
        return;
    }

    isSending = true;
    if (sendButton) {
        sendButton.disabled = true;
    }

    appendMessage("user", userMessage);
    inputElement.value = "";

    fetch(API_BASE_URL + "/integrated-chat", {
        method: "POST",
        headers: {
            "Content-Type": "application/json"
        },
        body: JSON.stringify({ message: userMessage })
    })
        .then(function(response) {
            return response.json().then(function(data) {
                if (!response.ok) {
                    throw new Error(data.detail || "서버 요청에 실패했습니다.");
                }
                return data;
            });
        })
        .then(function(data) {
            appendMessage("bot", data.answer || "응답을 생성하지 못했습니다.");
            console.log("RAG sources:", data.sources || []);
        })
        .catch(function(error) {
            appendMessage("bot", "오류가 발생했습니다: " + error.message);
            console.error(error);
        })
        .finally(function() {
            isSending = false;
            if (sendButton) {
                sendButton.disabled = false;
            }
        });
}

function toggleUpload() {
    const panel = document.getElementById("upload-panel");
    if (!panel) {
        return;
    }
    panel.style.display = panel.style.display === "none" || panel.style.display === "" ? "block" : "none";
}

document.addEventListener("DOMContentLoaded", function() {
    const inputElement = document.getElementById("user-input");
    if (!inputElement) {
        return;
    }

    inputElement.addEventListener("keydown", function(event) {
        if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            sendMessage();
        }
    });
});
