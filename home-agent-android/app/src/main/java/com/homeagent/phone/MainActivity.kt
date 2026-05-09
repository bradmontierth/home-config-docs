package com.homeagent.phone

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.media.MediaRecorder
import android.os.Bundle
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.MutableState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import okhttp3.Call
import okhttp3.Callback
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.asRequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.IOException


class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        setContent {
            MaterialTheme {
                Surface(modifier = Modifier.fillMaxSize(), color = Color(0xFF111312)) {
                    HomeAgentApp()
                }
            }
        }
    }
}


data class AgentSession(
    val sessionId: String,
    val title: String,
    val status: String,
    val startedAt: Double,
    val resumeFrom: String?
)


@Composable
fun HomeAgentApp() {
    val context = LocalContext.current
    val prefs = remember { context.getSharedPreferences("home-agent", Context.MODE_PRIVATE) }
    val client = remember { OkHttpClient() }
    val recorderState = remember { RecorderState(context) }

    var gatewayUrl by remember { mutableStateOf(prefs.getString("gateway_url", "http://192.168.10.217:8767") ?: "") }
    var token by remember { mutableStateOf(prefs.getString("token", "") ?: "") }
    var transcript by remember { mutableStateOf("") }
    var terminal by remember { mutableStateOf("") }
    var status by remember { mutableStateOf("Ready") }
    var sessionId by remember { mutableStateOf<String?>(null) }
    var socket by remember { mutableStateOf<WebSocket?>(null) }
    var sessionRunning by remember { mutableStateOf(false) }
    var sessions by remember { mutableStateOf<List<AgentSession>>(emptyList()) }
    var showSessions by remember { mutableStateOf(false) }
    var selectedSessionId by remember { mutableStateOf<String?>(null) }
    var selectedSessionTitle by remember { mutableStateOf<String?>(null) }

    fun attachSession(id: String) {
        sessionId = id
        selectedSessionId = id
        selectedSessionTitle = id
        sessionRunning = true
        status = "Session $id"
        socket = connectWebSocket(client, gatewayUrl, token, id) { event ->
            when (event.optString("type")) {
                "output" -> {
                    val data = event.optString("data")
                    terminal += data
                    if (data.contains("AWAITING_PHONE_APPROVAL:")) {
                        status = "Waiting for approval"
                    }
                }
                "status" -> {
                    val nextStatus = event.optString("status", status)
                    status = nextStatus
                    if (nextStatus == "exited" || nextStatus == "closed") {
                        sessionRunning = false
                    }
                }
            }
        }
    }

    fun resumeOrSend(text: String) {
        val liveSocket = socket
        if (sessionRunning && liveSocket != null) {
            liveSocket.send(JSONObject(mapOf("type" to "input", "data" to text)).toString())
            return
        }
        val target = selectedSessionId ?: sessionId
        if (target == null) {
            terminal += "\n[steer error] No session selected to resume.\n"
            status = "No session selected"
            return
        }
        status = "Resuming $target"
        resumeSession(client, gatewayUrl, token, target, text) { result ->
            result.onSuccess { id ->
                terminal += "\n[resume] Continuing $target as $id\n"
                attachSession(id)
            }.onFailure {
                status = "Resume failed"
                terminal += "\n[resume error] ${it.message}\n"
            }
        }
    }

    val requestPermission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        status = if (granted) "Microphone ready" else "Microphone denied"
    }

    LaunchedEffect(Unit) {
        if (ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            requestPermission.launch(Manifest.permission.RECORD_AUDIO)
        }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(14.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp)
    ) {
        Header(status = status, onSessions = {
            showSessions = !showSessions
            if (showSessions) {
                listSessions(client, gatewayUrl, token) { result ->
                    result.onSuccess {
                        sessions = it
                    }.onFailure {
                        terminal += "\n[sessions error] ${it.message}\n"
                    }
                }
            }
        }, onStop = {
            socket?.send("""{"type":"stop"}""")
            sessionId?.let { stopSession(client, gatewayUrl, token, it) }
            sessionRunning = false
            status = "Stopping"
        })

        ConnectionFields(
            gatewayUrl = gatewayUrl,
            token = token,
            onGatewayUrl = {
                gatewayUrl = it.trim()
                prefs.edit().putString("gateway_url", gatewayUrl).apply()
            },
            onToken = {
                token = it.trim()
                prefs.edit().putString("token", token).apply()
            }
        )

        if (showSessions) {
            SessionDrawer(
                sessions = sessions,
                selectedSessionId = selectedSessionId,
                onRefresh = {
                    listSessions(client, gatewayUrl, token) { result ->
                        result.onSuccess { sessions = it }
                            .onFailure { terminal += "\n[sessions error] ${it.message}\n" }
                    }
                },
                onSelect = {
                    selectedSessionId = it.sessionId
                    selectedSessionTitle = it.title
                    sessionId = it.sessionId
                    status = "Selected ${it.sessionId}"
                    showSessions = false
                }
            )
        }

        selectedSessionTitle?.let {
            Text("Selected: $it", color = Color(0xFFA9B2AA))
        }

        PushToTalkButton(
            isRecording = recorderState.isRecording.value,
            onStart = {
                try {
                    recorderState.start()
                    status = "Recording"
                } catch (error: Exception) {
                    status = "Recorder failed"
                    terminal += "\n[recorder error] ${error.message}\n"
                }
            },
            onStop = {
                try {
                    val file = recorderState.stop()
                    status = "Transcribing"
                    transcribe(client, gatewayUrl, token, file) { result ->
                        result.onSuccess {
                            transcript = it
                            status = "Transcript ready"
                        }.onFailure {
                            status = "Transcription failed"
                            terminal += "\n[transcription error] ${it.message}\n"
                        }
                    }
                } catch (error: Exception) {
                    status = "Recorder failed"
                    terminal += "\n[recorder error] ${error.message}\n"
                }
            }
        )

        OutlinedTextField(
            value = transcript,
            onValueChange = { transcript = it },
            label = { Text("Transcript") },
            modifier = Modifier.fillMaxWidth(),
            minLines = 3,
            maxLines = 5
        )

        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(
                onClick = {
                    val target = selectedSessionId
                    status = if (target == null) "Starting Codex" else "Resuming $target"
                    if (target == null) {
                        terminal = ""
                    }
                    val callback: (Result<String>) -> Unit = { result ->
                        result.onSuccess { id ->
                            attachSession(id)
                        }.onFailure {
                            status = if (target == null) "Start failed" else "Resume failed"
                            terminal += "\n[session error] ${it.message}\n"
                        }
                    }
                    if (target == null) {
                        startSession(client, gatewayUrl, token, transcript, callback)
                    } else {
                        resumeSession(client, gatewayUrl, token, target, transcript, callback)
                    }
                },
                enabled = transcript.isNotBlank() && gatewayUrl.isNotBlank()
            ) {
                Text(if (selectedSessionId == null) "Run Codex" else "Resume Codex")
            }

            TextButton(onClick = {
                transcript = ""
                terminal = ""
                selectedSessionId = null
                selectedSessionTitle = null
                status = "Ready"
            }) {
                Text("Clear")
            }
        }

        Terminal(terminal, Modifier.weight(1f))

        QuickActions(
            onSend = { text ->
                resumeOrSend(text)
            }
        )
    }
}


@Composable
fun Header(status: String, onSessions: () -> Unit, onStop: () -> Unit) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically
    ) {
        Column {
            Text("Home Agent", color = Color.White, fontWeight = FontWeight.Bold)
            Text(status, color = Color(0xFFA9B2AA))
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = onSessions) {
                Text("Sessions")
            }
            Button(
                onClick = onStop,
                colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF351A18))
            ) {
                Text("Stop")
            }
        }
    }
}


@Composable
fun ConnectionFields(
    gatewayUrl: String,
    token: String,
    onGatewayUrl: (String) -> Unit,
    onToken: (String) -> Unit
) {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        OutlinedTextField(
            value = gatewayUrl,
            onValueChange = onGatewayUrl,
            label = { Text("Gateway URL") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth()
        )
        OutlinedTextField(
            value = token,
            onValueChange = onToken,
            label = { Text("Token") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth()
        )
    }
}


@Composable
fun SessionDrawer(
    sessions: List<AgentSession>,
    selectedSessionId: String?,
    onRefresh: () -> Unit,
    onSelect: (AgentSession) -> Unit
) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .background(Color(0xFF1B201D), RoundedCornerShape(8.dp))
            .padding(10.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp)
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text("Sessions", color = Color.White, fontWeight = FontWeight.Bold)
            TextButton(onClick = onRefresh) {
                Text("Refresh")
            }
        }
        if (sessions.isEmpty()) {
            Text("No sessions found.", color = Color(0xFFA9B2AA))
        } else {
            Column(
                modifier = Modifier.heightIn(max = 220.dp).verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(6.dp)
            ) {
                sessions.forEach { session ->
                    val selected = session.sessionId == selectedSessionId
                    Button(
                        onClick = { onSelect(session) },
                        modifier = Modifier.fillMaxWidth(),
                        colors = ButtonDefaults.buttonColors(
                            containerColor = if (selected) Color(0xFF315B45) else Color(0xFF26302A)
                        )
                    ) {
                        Column(modifier = Modifier.fillMaxWidth()) {
                            Text(session.title.ifBlank { session.sessionId }, color = Color.White)
                            Text("${session.sessionId} · ${session.status}", color = Color(0xFFC7CEC8))
                        }
                    }
                }
            }
        }
    }
}


@Composable
fun PushToTalkButton(isRecording: Boolean, onStart: () -> Unit, onStop: () -> Unit) {
    val color = if (isRecording) Color(0xFFFF7067) else Color(0xFFE23B2F)
    Button(
        onClick = {},
        modifier = Modifier
            .fillMaxWidth()
            .heightIn(min = 140.dp)
            .pointerInput(Unit) {
                detectTapGestures(
                    onPress = {
                        onStart()
                        tryAwaitRelease()
                        onStop()
                    }
                )
            },
        shape = RoundedCornerShape(8.dp),
        colors = ButtonDefaults.buttonColors(containerColor = color)
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text(if (isRecording) "Recording" else "Hold", color = Color.White)
            Text("Talk", color = Color.White, fontWeight = FontWeight.Bold)
        }
    }
}


@Composable
fun Terminal(text: String, modifier: Modifier = Modifier) {
    val scrollState = rememberScrollState()
    LaunchedEffect(text.length) {
        scrollState.scrollTo(scrollState.maxValue)
    }
    SelectionContainer {
        Text(
            text = text.ifBlank { "Terminal output will appear here." },
            modifier = modifier
                .fillMaxWidth()
                .background(Color(0xFF070908), RoundedCornerShape(8.dp))
                .padding(10.dp)
                .verticalScroll(scrollState),
            color = Color(0xFFF4F2ED),
            fontFamily = FontFamily.Monospace
        )
    }
}


@Composable
fun QuickActions(onSend: (String) -> Unit) {
    var message by remember { mutableStateOf("") }
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = { onSend("Approved. Proceed with the proposed action.") }, modifier = Modifier.weight(1f)) {
                Text("Approve")
            }
            Button(onClick = { onSend("Do not proceed with that action. Explain a safer alternative.") }, modifier = Modifier.weight(1f)) {
                Text("Reject")
            }
            Button(onClick = { onSend("Pause and summarize what you have found so far.") }, modifier = Modifier.weight(1f)) {
                Text("Summary")
            }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedTextField(
                value = message,
                onValueChange = { message = it },
                label = { Text("Steer") },
                singleLine = true,
                modifier = Modifier.weight(1f)
            )
            Button(onClick = {
                if (message.isNotBlank()) {
                    onSend(message)
                    message = ""
                }
            }) {
                Text("Send")
            }
        }
    }
}


class RecorderState(private val context: Context) {
    val isRecording: MutableState<Boolean> = mutableStateOf(false)
    private var recorder: MediaRecorder? = null
    private var outputFile: File? = null

    fun start() {
        if (isRecording.value) return
        val file = File.createTempFile("home-agent-", ".m4a", context.cacheDir)
        outputFile = file
        @Suppress("DEPRECATION")
        recorder = MediaRecorder().apply {
            setAudioSource(MediaRecorder.AudioSource.MIC)
            setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
            setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
            setAudioSamplingRate(16000)
            setAudioChannels(1)
            setOutputFile(file.absolutePath)
            prepare()
            start()
        }
        isRecording.value = true
    }

    fun stop(): File {
        val file = outputFile ?: error("No recording file")
        recorder?.apply {
            stop()
            release()
        }
        recorder = null
        outputFile = null
        isRecording.value = false
        return file
    }
}


fun transcribe(
    client: OkHttpClient,
    gatewayUrl: String,
    token: String,
    file: File,
    callback: (Result<String>) -> Unit
) {
    val body = MultipartBody.Builder()
        .setType(MultipartBody.FORM)
        .addFormDataPart("audio", "voice.m4a", file.asRequestBody("audio/mp4".toMediaType()))
        .build()
    val request = Request.Builder()
        .url("${gatewayUrl.trimEnd('/')}/api/transcribe${tokenQuery(token)}")
        .post(body)
        .build()
    client.newCall(request).enqueue(resultCallback(callback) { response ->
        JSONObject(response.body?.string().orEmpty()).optString("text")
    })
}


fun startSession(
    client: OkHttpClient,
    gatewayUrl: String,
    token: String,
    transcript: String,
    callback: (Result<String>) -> Unit
) {
    val json = JSONObject(mapOf("text" to transcript)).toString()
    val request = Request.Builder()
        .url("${gatewayUrl.trimEnd('/')}/api/sessions${tokenQuery(token)}")
        .post(json.toRequestBody("application/json".toMediaType()))
        .build()
    client.newCall(request).enqueue(resultCallback(callback) { response ->
        JSONObject(response.body?.string().orEmpty()).getString("session_id")
    })
}


fun resumeSession(
    client: OkHttpClient,
    gatewayUrl: String,
    token: String,
    sessionId: String,
    text: String,
    callback: (Result<String>) -> Unit
) {
    val json = JSONObject(mapOf("text" to text)).toString()
    val request = Request.Builder()
        .url("${gatewayUrl.trimEnd('/')}/api/sessions/$sessionId/resume${tokenQuery(token)}")
        .post(json.toRequestBody("application/json".toMediaType()))
        .build()
    client.newCall(request).enqueue(resultCallback(callback) { response ->
        JSONObject(response.body?.string().orEmpty()).getString("session_id")
    })
}


fun listSessions(
    client: OkHttpClient,
    gatewayUrl: String,
    token: String,
    callback: (Result<List<AgentSession>>) -> Unit
) {
    val request = Request.Builder()
        .url("${gatewayUrl.trimEnd('/')}/api/sessions${tokenQuery(token)}")
        .get()
        .build()
    client.newCall(request).enqueue(resultCallback(callback) { response ->
        val array = JSONArray(response.body?.string().orEmpty())
        (0 until array.length()).map { index ->
            val item = array.getJSONObject(index)
            AgentSession(
                sessionId = item.getString("session_id"),
                title = item.optString("title", item.getString("session_id")),
                status = item.optString("status", "archived"),
                startedAt = item.optDouble("started_at", 0.0),
                resumeFrom = item.optString("resume_from").takeIf { it.isNotBlank() && it != "null" }
            )
        }
    })
}


fun stopSession(client: OkHttpClient, gatewayUrl: String, token: String, sessionId: String) {
    val request = Request.Builder()
        .url("${gatewayUrl.trimEnd('/')}/api/sessions/$sessionId/stop${tokenQuery(token)}")
        .post(ByteArray(0).toRequestBody(null))
        .build()
    client.newCall(request).enqueue(object : Callback {
        override fun onFailure(call: Call, e: IOException) = Unit
        override fun onResponse(call: Call, response: Response) {
            response.close()
        }
    })
}


fun connectWebSocket(
    client: OkHttpClient,
    gatewayUrl: String,
    token: String,
    sessionId: String,
    onEvent: (JSONObject) -> Unit
): WebSocket {
    val wsUrl = gatewayUrl
        .trimEnd('/')
        .replaceFirst("https://", "wss://")
        .replaceFirst("http://", "ws://")
    val request = Request.Builder()
        .url("$wsUrl/ws/sessions/$sessionId${tokenQuery(token)}")
        .build()
    return client.newWebSocket(request, object : WebSocketListener() {
        override fun onMessage(webSocket: WebSocket, text: String) {
            onMainThread {
                onEvent(JSONObject(text))
            }
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            onMainThread {
                onEvent(JSONObject(mapOf("type" to "output", "data" to "\n[websocket error] ${t.message}\n")))
            }
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            onMainThread {
                onEvent(JSONObject(mapOf("type" to "status", "status" to "closed")))
            }
        }
    })
}


fun <T> resultCallback(callback: (Result<T>) -> Unit, parser: (Response) -> T): Callback {
    return object : Callback {
        override fun onFailure(call: Call, e: IOException) {
            onMainThread { callback(Result.failure(e)) }
        }

        override fun onResponse(call: Call, response: Response) {
            response.use {
                if (!it.isSuccessful) {
                    onMainThread { callback(Result.failure(IOException(it.body?.string().orEmpty()))) }
                    return
                }
                val parsed = runCatching { parser(it) }
                onMainThread { callback(parsed) }
            }
        }
    }
}


fun tokenQuery(token: String): String {
    return if (token.isBlank()) "" else "?token=${java.net.URLEncoder.encode(token, "UTF-8")}"
}


fun onMainThread(block: () -> Unit) {
    android.os.Handler(android.os.Looper.getMainLooper()).post(block)
}
