import React from "react";
import ReactMarkdown from "react-markdown";

const AI_AVATAR =
  "https://static.prod-images.emergentagent.com/jobs/ed4e7d81-8953-45de-a23c-d48e941ecd1d/images/c7086dd78e0bfe64bf4737e1adc397f7dc41438f8c4eee76e4b486e0869312c3.png";

export default function ChatMessage({ message }) {
  const isUser = message.role === "user";

  if (isUser) {
    return (
      <div
        className="flex justify-end mb-6 fade-in-up"
        data-testid="chat-message-user"
      >
        <div className="max-w-[80%]">
          {message.has_image && (
            <div className="mb-2 text-xs font-mono text-[#ffd700] text-right">
              [image attached]
            </div>
          )}
          <div className="bg-[#ffd700] text-black border-2 border-black p-4 font-medium rounded-br-none shadow-[4px_4px_0_0_#ff2a6d] whitespace-pre-wrap break-words">
            {message.content}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div
      className="flex gap-4 mb-6 fade-in-up"
      data-testid="chat-message-assistant"
    >
      <img
        src={AI_AVATAR}
        alt="AI"
        className="w-10 h-10 border-2 border-white/30 object-cover flex-shrink-0"
      />
      <div className="flex-1 max-w-[80%]">
        <div className="text-xs uppercase tracking-[0.2em] text-[#05d9e8] font-bold mb-2">
          // GEMINI 3 PRO
        </div>
        <div className="bg-transparent text-white border-2 border-white/20 p-4 rounded-bl-none shadow-[4px_4px_0_0_rgba(5,217,232,0.4)]">
          <div className="md-body">
            <ReactMarkdown>{message.content}</ReactMarkdown>
          </div>
        </div>
      </div>
    </div>
  );
}
