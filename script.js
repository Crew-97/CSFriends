// script.js - [기본 코드] GPT와 직접 통신하기
// 목표: 백엔드 서버(/chat)로 메시지를 보내고, GPT 응답을 console.log로 확인하기

function sendMessage() {
    // 1. 입력창에서 사용자가 입력한 텍스트 가져오기
    const inputElement = document.getElementById("user-input");
    const userMessage = inputElement.value;

    // 빈 메시지면 실행 안 함
    if (userMessage === "") {
        console.log("메시지를 입력해주세요!");
        return;
    }

    console.log("=== 채팅 시작 ===");
    console.log("내가 보낸 메시지:", userMessage);

    // 2. 서버로 메시지 보내기 (fetch API 사용)
    fetch("http://localhost:8000/chat", {
        method: "POST",
        headers: {
            "Content-Type": "application/json"
        },
        body: JSON.stringify({ message: userMessage })
    })
    .then(function(response) {
        // 3. 서버 응답을 JSON으로 변환
        return response.json();
    })
    .then(function(data) {
        // 4. 콘솔에 결과 출력
        console.log("GPT의 답변:", data.answer);
        console.log("=== 채팅 끝 ===");

        // 입력창 비우기
        inputElement.value = "";
    })
    .catch(function(error) {
        console.error("오류 발생:", error);
        console.log("서버가 켜져 있는지 확인해주세요!");
    });
}
