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
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.lightColorScheme
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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
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


private val AppBackground = Color(0xFFF6F7F4)
private val Panel = Color(0xFFFFFFFF)
private val Ink = Color(0xFF1E2A26)
private val Muted = Color(0xFF62716B)
private val Primary = Color(0xFF1F7A6B)
private val PrimaryDark = Color(0xFF15594F)
private val Accent = Color(0xFFE4F2ED)
private val Danger = Color(0xFFC84132)
private val DangerSoft = Color(0xFFFFE9E4)


class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        setContent {
            MaterialTheme(
                colorScheme = lightColorScheme(
                    primary = Primary,
                    onPrimary = Color.White,
                    secondary = Color(0xFF45665D),
                    background = AppBackground,
                    surface = Panel,
                    onSurface = Ink,
                    error = Danger
                )
            ) {
                Surface(modifier = Modifier.fillMaxSize(), color = AppBackground) {
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
    var replyText by remember { mutableStateOf("") }
    var terminal by remember { mutableStateOf("") }
    var status by remember { mutableStateOf("Ready") }
    var sessionId by remember { mutableStateOf<String?>(null) }
    var socket by remember { mutableStateOf<WebSocket?>(null) }
    var sessionRunning by remember { mutableStateOf(false) }
    var sessions by remember { mutableStateOf<List<AgentSession>>(emptyList()) }
    var showSessions by remember { mutableStateOf(false) }
    var showSettings by remember { mutableStateOf(false) }
    var terminalExpanded by remember { mutableStateOf(false) }
    var selectedSessionId by remember { mutableStateOf<String?>(null) }
    var selectedSessionTitle by remember { mutableStateOf<String?>(null) }
    val hasCurrentSession = selectedSessionId != null || sessionId != null

    fun attachSession(id: String, title: String = id) {
        socket?.close(1000, "switch session")
        sessionId = id
        selectedSessionId = id
        selectedSessionTitle = title
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

    fun refreshSessions() {
        listSessions(client, gatewayUrl, token) { result ->
            result.onSuccess {
                sessions = it
            }.onFailure {
                terminal += "\n[sessions error] ${it.message}\n"
            }
        }
    }

    fun stopAgent() {
        socket?.send("""{"type":"stop"}""")
        sessionId?.let { stopSession(client, gatewayUrl, token, it) }
        sessionRunning = false
        status = "Stopping agent"
    }

    fun resumeOrSend(text: String) {
        val liveSocket = socket
        if (sessionRunning && liveSocket != null && status != "Waiting for approval") {
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

    fun finishRecording() {
        try {
            val file = recorderState.stop()
            status = "Transcribing"
            transcribe(client, gatewayUrl, token, file) { result ->
                result.onSuccess {
                    if (hasCurrentSession) {
                        replyText = it
                    } else {
                        transcript = it
                    }
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

    fun startRecording() {
        try {
            recorderState.start()
            status = "Recording"
        } catch (error: Exception) {
            status = "Recorder failed"
            terminal += "\n[recorder error] ${error.message}\n"
        }
    }

    fun runCodex() {
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
    }

    val requestPermission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        status = if (granted) "Microphone ready" else "Microphone denied"
    }

    LaunchedEffect(Unit) {
        if (ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            requestPermission.launch(Manifest.permission.RECORD_AUDIO)
        }
    }

    if (showSettings) {
        SettingsDialog(
            gatewayUrl = gatewayUrl,
            token = token,
            onGatewayUrl = {
                gatewayUrl = it.trim()
                prefs.edit().putString("gateway_url", gatewayUrl).apply()
            },
            onToken = {
                token = it.trim()
                prefs.edit().putString("token", token).apply()
            },
            onDismiss = { showSettings = false }
        )
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .statusBarsPadding()
            .navigationBarsPadding()
            .padding(horizontal = 16.dp, vertical = 10.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp)
    ) {
        if (terminalExpanded) {
            TalkButton(
                isRecording = recorderState.isRecording.value,
                compact = true,
                targetLabel = if (hasCurrentSession) "Reply" else "Task",
                onClick = {
                    if (recorderState.isRecording.value) finishRecording() else startRecording()
                }
            )

            Terminal(
                text = terminal,
                expanded = true,
                onExpand = { terminalExpanded = true },
                onCollapse = { terminalExpanded = false },
                modifier = Modifier.weight(1f)
            )

            StopAgentButton(onStop = ::stopAgent)

            QuickActions(
                replyText = replyText,
                onReplyTextChange = { replyText = it },
                onSend = ::resumeOrSend
            )

            return@Column
        }

        Header(
            status = status,
            onSessions = {
                showSessions = !showSessions
                if (showSessions) refreshSessions()
            },
            onSettings = { showSettings = true }
        )

        if (showSessions) {
            SessionDrawer(
                sessions = sessions,
                selectedSessionId = selectedSessionId,
                onRefresh = ::refreshSessions,
                onSelect = {
                    selectedSessionId = it.sessionId
                    selectedSessionTitle = it.title
                    sessionId = it.sessionId
                    status = "Loading ${it.sessionId}"
                    socket?.close(1000, "select archived session")
                    sessionRunning = false
                    fetchSessionLog(client, gatewayUrl, token, it.sessionId) { result ->
                        result.onSuccess { history ->
                            terminal = history
                            status = if (it.status == "running") {
                                "Session ${it.sessionId}"
                            } else {
                                "Selected ${it.sessionId}"
                            }
                            if (it.status == "running") {
                                attachSession(it.sessionId, it.title)
                            }
                        }.onFailure { error ->
                            terminal += "\n[history error] ${error.message}\n"
                            status = "History load failed"
                            if (it.status == "running") {
                                attachSession(it.sessionId, it.title)
                            }
                        }
                    }
                    showSessions = false
                }
            )
        }

        selectedSessionTitle?.let {
            Text("Selected: $it", color = Muted, style = MaterialTheme.typography.bodySmall)
        }

        TalkButton(
            isRecording = recorderState.isRecording.value,
            targetLabel = if (hasCurrentSession) "Reply" else "Task",
            onClick = {
                if (recorderState.isRecording.value) finishRecording() else startRecording()
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
                onClick = ::runCodex,
                enabled = transcript.isNotBlank() && gatewayUrl.isNotBlank(),
                modifier = Modifier.weight(1f),
                colors = ButtonDefaults.buttonColors(containerColor = Primary)
            ) {
                Text(if (selectedSessionId == null) "Run Codex" else "Resume Codex")
            }

            OutlinedButton(onClick = {
                transcript = ""
                replyText = ""
                terminal = ""
                selectedSessionId = null
                selectedSessionTitle = null
                status = "Ready"
            }) {
                Text("Clear")
            }
        }

        StopAgentButton(onStop = ::stopAgent)

        Terminal(
            text = terminal,
            expanded = false,
            onExpand = { terminalExpanded = true },
            onCollapse = { terminalExpanded = false },
            modifier = Modifier.weight(1f)
        )

        QuickActions(
            replyText = replyText,
            onReplyTextChange = { replyText = it },
            onSend = ::resumeOrSend
        )
    }
}


@Composable
fun Header(status: String, onSessions: () -> Unit, onSettings: () -> Unit) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .heightIn(min = 44.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text("Home Agent", color = Ink, fontWeight = FontWeight.Bold)
            Text(status, color = Muted, style = MaterialTheme.typography.bodySmall)
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
            OutlinedButton(onClick = onSessions) {
                Text("Sessions")
            }
            IconButton(onClick = onSettings) {
                Icon(painterResource(R.drawable.ic_settings_24), contentDescription = "Settings", tint = PrimaryDark)
            }
        }
    }
}


@Composable
fun SettingsDialog(
    gatewayUrl: String,
    token: String,
    onGatewayUrl: (String) -> Unit,
    onToken: (String) -> Unit,
    onDismiss: () -> Unit
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Settings") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
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
        },
        confirmButton = {
            Button(onClick = onDismiss, colors = ButtonDefaults.buttonColors(containerColor = Primary)) {
                Text("Done")
            }
        },
        containerColor = Panel
    )
}


@Composable
fun SessionDrawer(
    sessions: List<AgentSession>,
    selectedSessionId: String?,
    onRefresh: () -> Unit,
    onSelect: (AgentSession) -> Unit
) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(8.dp),
        colors = CardDefaults.cardColors(containerColor = Panel),
        border = BorderStroke(1.dp, Color(0xFFD7E2DD))
    ) {
        Column(
            modifier = Modifier.padding(10.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text("Sessions", color = Ink, fontWeight = FontWeight.Bold)
                TextButton(onClick = onRefresh) {
                    Text("Refresh", color = Primary)
                }
            }
            if (sessions.isEmpty()) {
                Text("No sessions found.", color = Muted)
            } else {
                Column(
                    modifier = Modifier
                        .heightIn(max = 220.dp)
                        .verticalScroll(rememberScrollState()),
                    verticalArrangement = Arrangement.spacedBy(6.dp)
                ) {
                    sessions.forEach { session ->
                        val selected = session.sessionId == selectedSessionId
                        Button(
                            onClick = { onSelect(session) },
                            modifier = Modifier.fillMaxWidth(),
                            colors = ButtonDefaults.buttonColors(
                                containerColor = if (selected) Primary else Accent,
                                contentColor = if (selected) Color.White else Ink
                            )
                        ) {
                            Column(modifier = Modifier.fillMaxWidth()) {
                                Text(session.title.ifBlank { session.sessionId }, fontWeight = FontWeight.Bold)
                                Text("${session.sessionId} - ${session.status}", style = MaterialTheme.typography.bodySmall)
                            }
                        }
                    }
                }
            }
        }
    }
}


@Composable
fun TalkButton(
    isRecording: Boolean,
    targetLabel: String,
    compact: Boolean = false,
    onClick: () -> Unit
) {
    val borderColor = if (isRecording) Danger else Primary
    val iconBackground = if (isRecording) DangerSoft else Accent
    val text = if (isRecording) "Tap to Finish" else "Press to Talk"
    val subtext = if (isRecording) "Recording $targetLabel" else "Tap once to dictate $targetLabel"
    val controlHeight = if (compact) 82.dp else 126.dp
    val iconSize = if (compact) 48.dp else 76.dp
    val micSize = if (compact) 28.dp else 42.dp

    Card(
        onClick = onClick,
        modifier = Modifier
            .fillMaxWidth()
            .height(controlHeight),
        shape = RoundedCornerShape(8.dp),
        colors = CardDefaults.cardColors(containerColor = Panel),
        border = BorderStroke(1.dp, borderColor)
    ) {
        Row(
            modifier = Modifier
                .fillMaxSize()
                .padding(18.dp),
            horizontalArrangement = Arrangement.spacedBy(18.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Box(
                modifier = Modifier
                    .size(iconSize)
                    .background(iconBackground, CircleShape),
                contentAlignment = Alignment.Center
            ) {
                Icon(
                    painterResource(R.drawable.ic_mic_24),
                    contentDescription = null,
                    tint = borderColor,
                    modifier = Modifier.size(micSize)
                )
            }
            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(
                    text,
                    color = Ink,
                    fontWeight = FontWeight.Bold,
                    style = if (compact) MaterialTheme.typography.titleMedium else MaterialTheme.typography.titleLarge
                )
                Text(subtext, color = Muted, style = MaterialTheme.typography.bodySmall)
            }
        }
    }
}

@Composable
fun StopAgentButton(onStop: () -> Unit) {
    Button(
        onClick = onStop,
        modifier = Modifier
            .fillMaxWidth()
            .height(58.dp),
        shape = RoundedCornerShape(8.dp),
        colors = ButtonDefaults.buttonColors(containerColor = Danger)
    ) {
        Icon(painterResource(R.drawable.ic_stop_24), contentDescription = null, modifier = Modifier.size(24.dp))
        Text(" Stop Agent Now", fontWeight = FontWeight.Bold)
    }
}


@Composable
fun Terminal(
    text: String,
    expanded: Boolean,
    onExpand: () -> Unit,
    onCollapse: () -> Unit,
    modifier: Modifier = Modifier
) {
    val scrollState = rememberScrollState()
    val terminalText = remember(text) { terminalAnnotatedString(text) }
    LaunchedEffect(text.length) {
        scrollState.scrollTo(scrollState.maxValue)
    }

    Card(
        onClick = onExpand,
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(8.dp),
        colors = CardDefaults.cardColors(containerColor = Color(0xFF22312D))
    ) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(10.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text("Terminal", color = Color(0xFFDCE8E1), fontWeight = FontWeight.Bold)
                TextButton(onClick = if (expanded) onCollapse else onExpand) {
                    Text(if (expanded) "Collapse" else "Expand", color = Color(0xFFB7E3D4))
                }
            }
            SelectionContainer(
                modifier = Modifier
                    .fillMaxWidth()
                    .weight(1f)
            ) {
                Text(
                    text = terminalText,
                    modifier = Modifier
                        .fillMaxWidth()
                        .verticalScroll(scrollState),
                    fontFamily = FontFamily.Monospace
                )
            }
        }
    }
}


fun terminalAnnotatedString(text: String) = buildAnnotatedString {
    val value = text.ifBlank { "Terminal output will appear here." }
    val lines = value.split('\n')
    lines.forEachIndexed { index, line ->
        val style = terminalLineStyle(line)
        pushStyle(style)
        append(line)
        pop()
        if (index < lines.lastIndex) {
            append("\n")
        }
    }
}


fun terminalLineStyle(line: String): SpanStyle {
    val trimmed = line.trim()
    return when {
        trimmed.contains("AWAITING_PHONE_APPROVAL:") -> SpanStyle(
            color = Color(0xFFFFD27A),
            fontWeight = FontWeight.Bold
        )
        trimmed.startsWith("[codex error]") ||
            trimmed.startsWith("[codex failed]") ||
            trimmed.contains(" error]") -> SpanStyle(
                color = Color(0xFFFF9A90),
                fontWeight = FontWeight.Medium
            )
        trimmed.startsWith("$ ") -> SpanStyle(
            color = Color(0xFFA8BEDA),
            fontWeight = FontWeight.Medium
        )
        trimmed.startsWith("[read ") ||
            trimmed.contains(" output hidden:") ||
            trimmed.startsWith("[search output hidden:") ||
            trimmed.startsWith("[query output hidden:") ||
            trimmed.startsWith("[request output hidden:") ||
            trimmed.startsWith("[logs output hidden:") -> SpanStyle(
                color = Color(0xFF7F8A82)
            )
        trimmed.startsWith("[codex]") ||
            trimmed.startsWith("[resume]") -> SpanStyle(
                color = Color(0xFFB7C7B8)
            )
        trimmed.startsWith("[") && trimmed.endsWith("]") -> SpanStyle(
            color = Color(0xFF9AA49C)
        )
        else -> SpanStyle(
            color = Color(0xFFF4F2ED),
            fontWeight = FontWeight.Normal
        )
    }
}


@Composable
fun QuickActions(
    replyText: String,
    onReplyTextChange: (String) -> Unit,
    onSend: (String) -> Unit
) {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = { onSend("Approved. Proceed with the proposed action.") }, modifier = Modifier.weight(1f), colors = ButtonDefaults.buttonColors(containerColor = Primary)) {
                Text("Approve")
            }
            OutlinedButton(onClick = { onSend("Do not proceed with that action. Explain a safer alternative.") }, modifier = Modifier.weight(1f)) {
                Text("Reject")
            }
            OutlinedButton(onClick = { onSend("Pause and summarize what you have found so far.") }, modifier = Modifier.weight(1f)) {
                Text("Summary")
            }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
            OutlinedTextField(
                value = replyText,
                onValueChange = onReplyTextChange,
                label = { Text("Reply") },
                singleLine = true,
                modifier = Modifier.weight(1f)
            )
            Button(onClick = {
                if (replyText.isNotBlank()) {
                    onSend(replyText)
                    onReplyTextChange("")
                }
            }, colors = ButtonDefaults.buttonColors(containerColor = Primary)) {
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


fun fetchSessionLog(
    client: OkHttpClient,
    gatewayUrl: String,
    token: String,
    sessionId: String,
    callback: (Result<String>) -> Unit
) {
    val request = Request.Builder()
        .url("${gatewayUrl.trimEnd('/')}/api/sessions/$sessionId/log${tokenQuery(token)}")
        .get()
        .build()
    client.newCall(request).enqueue(resultCallback(callback) { response ->
        JSONObject(response.body?.string().orEmpty()).optString("text")
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
