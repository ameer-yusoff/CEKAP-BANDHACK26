document.addEventListener('DOMContentLoaded', () => {
    const micButton = document.getElementById('micButton');
    const statusText = document.getElementById('statusText');
    const transcriptOutput = document.getElementById('transcriptOutput');

    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    let recognition;
    let isListening = false;
    let isProcessing = false; 

    if (SpeechRecognition) {
        recognition = new SpeechRecognition();
        recognition.continuous = false;
        recognition.interimResults = false;

        recognition.onstart = () => {
            isListening = true;
            micButton.classList.add('listening');
            statusText.textContent = "Listening...";
            statusText.style.color = "var(--danger-red)";
            transcriptOutput.textContent = "...";
            transcriptOutput.classList.remove('placeholder-text');
        };

        recognition.onresult = (event) => {
            const transcript = event.results[0][0].transcript;
            transcriptOutput.textContent = `"${transcript}"`;
            processWithAI(transcript);
        };

        recognition.onerror = (event) => {
            console.error("Speech API Error:", event.error);
            statusText.textContent = "Mic error: " + event.error;
            micButton.classList.remove('listening');
            isListening = false;
        };

        recognition.onend = () => {
            micButton.classList.remove('listening');
            isListening = false;
        };
    } else {
        statusText.textContent = "Browser not supported for speech recognition.";
        micButton.style.display = 'none';
    }

    let audioUnlocked = false;

    micButton.addEventListener('click', () => {
        if (isProcessing) return; 

        if (!audioUnlocked) {
            const unlockUtterance = new SpeechSynthesisUtterance('');
            window.speechSynthesis.speak(unlockUtterance);
            audioUnlocked = true;
        }

        window.speechSynthesis.cancel(); 

        if (isListening) {
            recognition.stop();
        } else {
            try {
                recognition.lang = 'ms-MY'; 
                recognition.start();
            } catch (e) {
                console.error("Recognition stuck:", e);
                recognition.stop();
                setTimeout(() => recognition.start(), 200); 
            }
        }
    });

    function speakText(text) {
        const synth = window.speechSynthesis;
        const utterance = new SpeechSynthesisUtterance(text);
        utterance.lang = 'ms-MY';
        utterance.rate = 0.95; 
        
        utterance.onend = () => {
            isProcessing = false; 
        };
        
        synth.speak(utterance);
    }

    async function processWithAI(text) {
        isProcessing = true; 
        statusText.textContent = "Sending to CEKAP AI...";
        statusText.style.color = "var(--text-secondary)";

        try {
            const response = await fetch('https://cekap-backend.onrender.com/api/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: text })
            });

            const data = await response.json();

            if (data.status === 'TERMINATE_CALL') {
                transcriptOutput.textContent = "Call terminated. Fake call usage detected.";
                statusText.textContent = "CALL TERMINATED";
                statusText.style.color = "var(--danger-red)";
                speakText("Call terminated.");
                micButton.disabled = true; 
                micButton.style.opacity = '0.3';
                return;
            }

            transcriptOutput.textContent = data.reply;
            statusText.textContent = "Response received.";
            speakText(data.reply);

        } catch (error) {
            console.error('API Error:', error);
            statusText.textContent = "Failed to connect to the server.";
            isProcessing = false; 
        }
    }
});