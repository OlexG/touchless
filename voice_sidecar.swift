import AVFoundation
import Foundation
import Speech

let logURL = URL(fileURLWithPath: "/tmp/touchless-voice.log")

struct ParsedCommand: Equatable {
    let command: String
    let text: String?
}

struct CommandPhraseMatch {
    let phrase: String
    let range: Range<String.Index>
}

let startDictationPhrases = [
    "type start",
    "start typing",
    "start type",
    "begin typing",
    "begin type",
]
let endDictationPhrases = [
    "type end",
    "end typing",
    "end type",
    "stop typing",
    "stop type",
    "finish typing",
    "finish type",
    "done typing",
]
let clearDictationPhrases = [
    "type clear",
    "clear typing",
    "clear type",
    "clear dictation",
    "scratch that",
    "delete that",
]
let clickCommandPhrases = [
    "click",
    "click it",
    "click that",
    "left click",
    "tap",
    "tap it",
    "tap that",
    "press",
    "press it",
    "press that",
    "select",
    "select it",
    "select that",
]

func log(_ message: String) {
    let timestamp = ISO8601DateFormatter().string(from: Date())
    guard let data = "\(timestamp) \(message)\n".data(using: .utf8) else {
        return
    }

    if FileManager.default.fileExists(atPath: logURL.path),
       let handle = try? FileHandle(forWritingTo: logURL) {
        defer { try? handle.close() }
        _ = try? handle.seekToEnd()
        try? handle.write(contentsOf: data)
    } else {
        try? data.write(to: logURL)
    }
}

func emit(_ payload: [String: Any]) {
    guard JSONSerialization.isValidJSONObject(payload),
          let data = try? JSONSerialization.data(withJSONObject: payload),
          let line = String(data: data, encoding: .utf8) else {
        return
    }
    if let output = "\(line)\n".data(using: .utf8) {
        FileHandle.standardOutput.write(output)
    }
    log("emit: \(line)")
}

func parseVoiceCommand(_ transcript: String) -> ParsedCommand? {
    let trimmed = transcript.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !trimmed.isEmpty else {
        return nil
    }

    let normalized = normalizeForCommand(trimmed)
    let commandText = stripActivationWords(normalized)

    if isClickCommand(commandText) {
        return ParsedCommand(command: "click", text: nil)
    }

    guard let rawText = extractDictationText(from: trimmed) else {
        return nil
    }

    let text = cleanDictatedText(rawText)
    guard !text.isEmpty else { return nil }
    return ParsedCommand(command: "type", text: text)
}

func normalizeForCommand(_ text: String) -> String {
    let lower = text.lowercased()
    let allowed = CharacterSet.alphanumerics.union(.whitespaces)
    let scalars = lower.unicodeScalars.map { scalar in
        allowed.contains(scalar) ? Character(scalar) : " "
    }
    return String(scalars)
        .split(separator: " ")
        .joined(separator: " ")
}

func stripActivationWords(_ text: String) -> String {
    var words = text.split(separator: " ").map(String.init)

    if words.first == "please" {
        words.removeFirst()
    }

    if words.count >= 2 && words[0] == "hey" && words[1] == "touchless" {
        words.removeFirst(2)
    } else if words.first == "touchless" {
        words.removeFirst()
    }

    return words.joined(separator: " ")
}

func isClickCommand(_ text: String) -> Bool {
    let clickCommands = Set(clickCommandPhrases)
    return clickCommands.contains(text)
}

func extractDictationText(from transcript: String) -> String? {
    let pattern = #"^\s*(?:please\s+)?(?:(?:hey\s+)?touchless\s+)?(?:type|write|dictate|enter text|insert text)\s+(.*)$"#
    guard let regex = try? NSRegularExpression(
        pattern: pattern,
        options: [.caseInsensitive]
    ) else {
        return nil
    }

    let range = NSRange(transcript.startIndex..<transcript.endIndex, in: transcript)
    guard let match = regex.firstMatch(in: transcript, range: range),
          match.numberOfRanges > 1,
          let textRange = Range(match.range(at: 1), in: transcript) else {
        return nil
    }

    return String(transcript[textRange])
}

func rangeOfCommandPhrase(_ phrase: String, in transcript: String) -> Range<String.Index>? {
    return commandPhraseRanges(phrase, in: transcript).first
}

func commandPhraseRanges(_ phrase: String, in transcript: String) -> [Range<String.Index>] {
    let escaped = NSRegularExpression.escapedPattern(for: phrase)
        .replacingOccurrences(of: "\\ ", with: #"\s+"#)
    let pattern = #"\b"# + escaped + #"\b"#
    guard let regex = try? NSRegularExpression(
        pattern: pattern,
        options: [.caseInsensitive]
    ) else {
        return []
    }

    let range = NSRange(transcript.startIndex..<transcript.endIndex, in: transcript)
    return regex.matches(in: transcript, range: range).compactMap { match in
        Range(match.range, in: transcript)
    }
}

func commandPhraseMatch(
    _ phrases: [String],
    in transcript: String,
    preferLast: Bool = false
) -> CommandPhraseMatch? {
    let matches = phrases.flatMap { phrase in
        commandPhraseRanges(phrase, in: transcript).map { range in
            CommandPhraseMatch(phrase: phrase, range: range)
        }
    }

    if preferLast {
        return matches.max { left, right in
            left.range.lowerBound < right.range.lowerBound
        }
    }

    return matches.min { left, right in
        left.range.lowerBound < right.range.lowerBound
    }
}

func transcriptEndsWithClickCommand(_ transcript: String) -> Bool {
    let normalized = stripActivationWords(normalizeForCommand(transcript))
    if isClickCommand(normalized) {
        return true
    }

    return clickCommandPhrases.contains { phrase in
        normalized == phrase || normalized.hasSuffix(" \(phrase)")
    }
}

func shouldResetIdleTranscript(_ transcript: String) -> Bool {
    return normalizeForCommand(transcript).split(separator: " ").count > 20
}

func cleanDictatedText(_ rawText: String) -> String {
    var text = rawText.trimmingCharacters(in: .whitespacesAndNewlines)
    text = text.trimmingCharacters(in: CharacterSet(charactersIn: "\"'“”‘’"))

    let replacements: [(String, String)] = [
        (#"\bperiod\b"#, "."),
        (#"\bfull stop\b"#, "."),
        (#"\bcomma\b"#, ","),
        (#"\bquestion mark\b"#, "?"),
        (#"\bexclamation point\b"#, "!"),
        (#"\bexclamation mark\b"#, "!"),
        (#"\bcolon\b"#, ":"),
        (#"\bsemicolon\b"#, ";"),
    ]

    for (pattern, replacement) in replacements {
        text = text.replacingOccurrences(
            of: pattern,
            with: replacement,
            options: [.regularExpression, .caseInsensitive]
        )
    }

    let fillerPattern = #"\b(um|uh|umm|er|ah)\b\s*"#
    text = text.replacingOccurrences(
        of: fillerPattern,
        with: "",
        options: [.regularExpression, .caseInsensitive]
    )

    text = text.replacingOccurrences(
        of: #"\s+([,.;:!?])"#,
        with: "$1",
        options: .regularExpression
    )
    text = text.replacingOccurrences(
        of: #"\s{2,}"#,
        with: " ",
        options: .regularExpression
    )

    return text.trimmingCharacters(in: .whitespacesAndNewlines)
}

func shouldExecuteClick(transcript: String, isFinal: Bool) -> Bool {
    if isFinal {
        return true
    }

    return transcriptEndsWithClickCommand(transcript)
}

func requestSpeechPermission() -> Bool {
    let semaphore = DispatchSemaphore(value: 0)
    var granted = false

    SFSpeechRecognizer.requestAuthorization { status in
        granted = status == .authorized
        semaphore.signal()
    }

    semaphore.wait()
    return granted
}

func requestMicrophonePermission() -> Bool {
    let semaphore = DispatchSemaphore(value: 0)
    var granted = false

    AVCaptureDevice.requestAccess(for: .audio) { allowed in
        granted = allowed
        semaphore.signal()
    }

    semaphore.wait()
    return granted
}

final class VoiceCommandRunner {
    private let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
    private let audioEngine = AVAudioEngine()
    private var recognitionRequest: SFSpeechAudioBufferRecognitionRequest?
    private var recognitionTask: SFSpeechRecognitionTask?
    private var recognitionGeneration = 0
    private var restarting = false
    private var lastClickAt = Date.distantPast
    private var lastTypedText: String?
    private var lastTypedAt = Date.distantPast
    private var pendingTypeWorkItem: DispatchWorkItem?
    private let typeEndpointDelay: TimeInterval = 0.85
    private var dictationActive = false
    private var dictationBuffer = ""
    private var currentDictationSegment = ""

    func run() {
        guard let recognizer else {
            emit(["type": "voice_error", "message": "Speech recognizer is unavailable"])
            return
        }

        emit(["type": "voice_status", "stage": "requesting_speech", "listening": false])
        guard requestSpeechPermission() else {
            emit(["type": "voice_error", "message": "Speech Recognition permission is blocked for Touchless"])
            return
        }

        emit(["type": "voice_status", "stage": "requesting_microphone", "listening": false])
        guard requestMicrophonePermission() else {
            emit(["type": "voice_error", "message": "Microphone permission is blocked for Touchless"])
            return
        }

        guard recognizer.isAvailable else {
            emit(["type": "voice_error", "message": "Speech recognizer is not currently available"])
            return
        }

        do {
            try startRecognition()
            RunLoop.main.run()
        } catch {
            emit(["type": "voice_error", "message": "Voice listener failed: \(error.localizedDescription)"])
        }
    }

    private func startRecognition() throws {
        stopRecognition()

        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        recognitionRequest = request

        let inputNode = audioEngine.inputNode
        let format = inputNode.outputFormat(forBus: 0)
        inputNode.installTap(onBus: 0, bufferSize: 1024, format: format) { buffer, _ in
            request.append(buffer)
        }

        audioEngine.prepare()
        try audioEngine.start()

        recognitionGeneration += 1
        let generation = recognitionGeneration
        emit(["type": "voice_status", "stage": "listening", "listening": true])
        recognitionTask = recognizer?.recognitionTask(with: request) { [weak self] result, error in
            guard let self else {
                return
            }

            if let result {
                DispatchQueue.main.async {
                    guard generation == self.recognitionGeneration else {
                        return
                    }
                    self.handle(result)
                }
            }

            if error != nil || result?.isFinal == true {
                DispatchQueue.main.async {
                    guard generation == self.recognitionGeneration else {
                        return
                    }
                    self.restartSoon()
                }
            }
        }
    }

    private func handle(_ result: SFSpeechRecognitionResult) {
        let transcript = result.bestTranscription.formattedString
        emit([
            "type": "voice_transcript",
            "transcript": transcript,
            "final": result.isFinal,
        ])

        if dictationActive {
            handleActiveDictation(transcript: transcript, isFinal: result.isFinal)
            return
        }

        if let startMatch = commandPhraseMatch(
            startDictationPhrases,
            in: transcript,
            preferLast: true
        ) {
            beginDictation(transcript: transcript, startRange: startMatch.range)
            return
        }

        guard let parsed = parseVoiceCommand(transcript) else {
            if shouldResetIdleTranscript(transcript) {
                restartSoon()
            }
            return
        }

        if parsed.command == "click", shouldExecuteClick(transcript: transcript, isFinal: result.isFinal) {
            cancelPendingType()
            emitClick(transcript: transcript)
            return
        }
    }

    private func beginDictation(
        transcript: String,
        startRange: Range<String.Index>
    ) {
        cancelPendingType()
        dictationActive = true
        dictationBuffer = ""
        currentDictationSegment = ""
        emit(["type": "voice_status", "stage": "dictating", "listening": true])
        restartSoon()
    }

    private func handleActiveDictation(transcript: String, isFinal: Bool) {
        handleDictationContent(transcript, originalTranscript: transcript, isFinal: isFinal)
    }

    private func handleDictationContent(
        _ content: String,
        originalTranscript: String,
        isFinal: Bool
    ) {
        if commandPhraseMatch(clearDictationPhrases, in: content) != nil {
            dictationBuffer = ""
            currentDictationSegment = ""
            emit([
                "type": "voice_status",
                "stage": "dictation_cleared",
                "listening": true,
            ])
            restartSoon()
            return
        }

        if let endMatch = commandPhraseMatch(endDictationPhrases, in: content) {
            let finalChunk = String(content[..<endMatch.range.lowerBound])
            let text = joinedDictationText(dictationBuffer, finalChunk)
            dictationActive = false
            dictationBuffer = ""
            currentDictationSegment = ""

            if text.isEmpty {
                emit(["type": "voice_status", "stage": "dictation_empty", "listening": true])
                restartSoon()
            } else {
                emitType(text, transcript: originalTranscript)
            }
            return
        }

        if isFinal {
            dictationBuffer = joinedDictationText(dictationBuffer, content)
            currentDictationSegment = ""
            emit(["type": "voice_status", "stage": "dictating", "listening": true])
        } else {
            currentDictationSegment = cleanDictatedText(content)
        }
    }

    private func joinedDictationText(_ existing: String, _ next: String) -> String {
        let cleanedNext = cleanDictatedText(next)
        if existing.isEmpty {
            return cleanedNext
        }
        if cleanedNext.isEmpty {
            return existing
        }
        return "\(existing) \(cleanedNext)"
    }

    private func emitClick(transcript: String) {
        let now = Date()
        guard now.timeIntervalSince(lastClickAt) >= 0.8 else {
            return
        }
        lastClickAt = now
        emit(["type": "voice_command", "command": "click", "transcript": transcript])
        restartSoon()
    }

    private func emitType(_ text: String, transcript: String) {
        let now = Date()
        if lastTypedText == text && now.timeIntervalSince(lastTypedAt) < 2.0 {
            return
        }
        lastTypedText = text
        lastTypedAt = now
        emit([
            "type": "voice_command",
            "command": "type",
            "text": text,
            "transcript": transcript,
        ])
        restartSoon()
    }

    private func scheduleType(_ text: String, transcript: String) {
        pendingTypeWorkItem?.cancel()

        let workItem = DispatchWorkItem { [weak self] in
            self?.emitType(text, transcript: transcript)
        }
        pendingTypeWorkItem = workItem

        DispatchQueue.main.asyncAfter(
            deadline: .now() + typeEndpointDelay,
            execute: workItem
        )
    }

    private func cancelPendingType() {
        pendingTypeWorkItem?.cancel()
        pendingTypeWorkItem = nil
    }

    private func restartSoon() {
        guard !restarting else {
            return
        }

        restarting = true
        stopRecognition()
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) { [weak self] in
            guard let self else {
                return
            }
            self.restarting = false
            do {
                try self.startRecognition()
            } catch {
                emit(["type": "voice_error", "message": "Voice listener restart failed: \(error.localizedDescription)"])
            }
        }
    }

    private func stopRecognition() {
        recognitionGeneration += 1
        cancelPendingType()
        recognitionTask?.cancel()
        recognitionTask = nil
        recognitionRequest?.endAudio()
        recognitionRequest = nil

        if audioEngine.isRunning {
            audioEngine.stop()
        }
        audioEngine.inputNode.removeTap(onBus: 0)
    }
}

if CommandLine.arguments.contains("--self-test") {
    let cases: [(String, ParsedCommand?)] = [
        ("click", ParsedCommand(command: "click", text: nil)),
        ("click it", ParsedCommand(command: "click", text: nil)),
        ("please click that", ParsedCommand(command: "click", text: nil)),
        ("tap it", ParsedCommand(command: "click", text: nil)),
        ("hey Touchless left click", ParsedCommand(command: "click", text: nil)),
        ("type hello world", ParsedCommand(command: "type", text: "hello world")),
        ("type \"hello\"", ParsedCommand(command: "type", text: "hello")),
        ("please type um hello comma world period", ParsedCommand(command: "type", text: "hello, world.")),
        ("Touchless write ship it", ParsedCommand(command: "type", text: "ship it")),
        ("hello", nil),
    ]

    for (input, expected) in cases {
        let actual = parseVoiceCommand(input)
        if actual != expected {
            emit([
                "type": "voice_error",
                "message": "self-test failed for \(input)",
            ])
            exit(1)
        }
    }

    let startEndInput = "type start hello comma world type end"
    guard let startRange = commandPhraseMatch(
        startDictationPhrases,
        in: startEndInput,
        preferLast: true
    )?.range else {
        emit(["type": "voice_error", "message": "self-test failed to find type start"])
        exit(1)
    }
    let afterStart = String(startEndInput[startRange.upperBound...])
    guard let endRange = commandPhraseMatch(endDictationPhrases, in: afterStart)?.range else {
        emit(["type": "voice_error", "message": "self-test failed to find type end"])
        exit(1)
    }
    let capturedText = cleanDictatedText(String(afterStart[..<endRange.lowerBound]))
    if capturedText != "hello, world" {
        emit([
            "type": "voice_error",
            "message": "self-test failed start/end capture: \(capturedText)",
        ])
        exit(1)
    }

    let clearInput = "type start hello type clear goodbye comma world type end"
    guard let clearStartRange = commandPhraseMatch(
        startDictationPhrases,
        in: clearInput,
        preferLast: true
    )?.range else {
        emit(["type": "voice_error", "message": "self-test failed to find clear type start"])
        exit(1)
    }
    let afterClearStart = String(clearInput[clearStartRange.upperBound...])
    guard let clearRange = commandPhraseMatch(clearDictationPhrases, in: afterClearStart)?.range else {
        emit(["type": "voice_error", "message": "self-test failed to find type clear"])
        exit(1)
    }
    let afterClear = String(afterClearStart[clearRange.upperBound...])
    guard let clearEndRange = commandPhraseMatch(endDictationPhrases, in: afterClear)?.range else {
        emit(["type": "voice_error", "message": "self-test failed to find clear type end"])
        exit(1)
    }
    let clearCapturedText = cleanDictatedText(String(afterClear[..<clearEndRange.lowerBound]))
    if clearCapturedText != "goodbye, world" {
        emit([
            "type": "voice_error",
            "message": "self-test failed clear capture: \(clearCapturedText)",
        ])
        exit(1)
    }

    let aliasInput = "start typing cursor is too sensitive end typing"
    guard let aliasStartRange = commandPhraseMatch(
        startDictationPhrases,
        in: aliasInput,
        preferLast: true
    )?.range else {
        emit(["type": "voice_error", "message": "self-test failed alias start"])
        exit(1)
    }
    let afterAliasStart = String(aliasInput[aliasStartRange.upperBound...])
    guard let aliasEndRange = commandPhraseMatch(endDictationPhrases, in: afterAliasStart)?.range else {
        emit(["type": "voice_error", "message": "self-test failed alias end"])
        exit(1)
    }
    let aliasCapturedText = cleanDictatedText(String(afterAliasStart[..<aliasEndRange.lowerBound]))
    if aliasCapturedText != "cursor is too sensitive" {
        emit([
            "type": "voice_error",
            "message": "self-test failed alias capture: \(aliasCapturedText)",
        ])
        exit(1)
    }

    let noisyInput = "start typing old text stop typing random words type start new text type end"
    guard let noisyStartRange = commandPhraseMatch(
        startDictationPhrases,
        in: noisyInput,
        preferLast: true
    )?.range else {
        emit(["type": "voice_error", "message": "self-test failed noisy start"])
        exit(1)
    }
    let afterNoisyStart = String(noisyInput[noisyStartRange.upperBound...])
    guard let noisyEndRange = commandPhraseMatch(endDictationPhrases, in: afterNoisyStart)?.range else {
        emit(["type": "voice_error", "message": "self-test failed noisy end"])
        exit(1)
    }
    let noisyCapturedText = cleanDictatedText(String(afterNoisyStart[..<noisyEndRange.lowerBound]))
    if noisyCapturedText != "new text" {
        emit([
            "type": "voice_error",
            "message": "self-test failed noisy capture: \(noisyCapturedText)",
        ])
        exit(1)
    }

    if !transcriptEndsWithClickCommand("old stale transcript and then click") {
        emit(["type": "voice_error", "message": "self-test failed stale click suffix"])
        exit(1)
    }

    emit(["type": "voice_status", "stage": "self_test_passed", "listening": false])
    exit(0)
}

log("voice sidecar starting")
let runner = VoiceCommandRunner()
runner.run()
