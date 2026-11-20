package com.homeagent.phone

import android.Manifest
import android.content.ActivityNotFoundException
import android.content.ClipData
import android.content.ClipboardManager
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.media.MediaRecorder
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.enableEdgeToEdge
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.DropdownMenu
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.widthIn
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
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.MutableState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.withLink
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import com.google.firebase.messaging.FirebaseMessaging
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
import org.json.JSONException
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.delay
import android.content.ComponentName
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.session.MediaController
import androidx.media3.session.SessionToken


private val AppBackground = Color(0xFFF6F7F4)
private val Panel = Color(0xFFFFFFFF)
private val Ink = Color(0xFF1E2A26)
private val Muted = Color(0xFF62716B)
private val Primary = Color(0xFF1F7A6B)
private val PrimaryDark = Color(0xFF15594F)
private val Accent = Color(0xFFE4F2ED)
private val Danger = Color(0xFFC84132)
private val DangerSoft = Color(0xFFFFE9E4)
private const val EXTRA_SESSION_ID = "com.homeagent.phone.SESSION_ID"
private const val FCM_DATA_SESSION_ID = "session_id"
private const val NOTIFICATION_CHANNEL_ID = "home_agent_sessions"
private const val SESSION_NOTIFICATION_ID = 4107


class MainActivity : ComponentActivity() {
    private val launchSessionId = mutableStateOf<String?>(null)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Only a fresh launch carries a request: a recreated activity gets its
        // original launch intent back, which would replay an old notification
        // tap and jump to that session.
        if (savedInstanceState == null) launchSessionId.value = intent.homeAgentSessionId()
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        // Edge-to-edge so the keyboard reaches Compose as an inset only. Without
        // it the window shrank for the keyboard AND imePadding() added the same
        // height again, squeezing the terminal behind a blank band.
        enableEdgeToEdge()
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
                    HomeAgentApp(launchSessionId)
                }
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        launchSessionId.value = intent.homeAgentSessionId()
    }

    override fun onResume() {
        super.onResume()
        InAppAlerts.foreground = true
    }

    override fun onPause() {
        InAppAlerts.foreground = false
        super.onPause()
    }
}


private fun Intent.homeAgentSessionId(): String? {
    return getStringExtra(EXTRA_SESSION_ID) ?: getStringExtra(FCM_DATA_SESSION_ID)
}


// A Claude/Codex session on the box that Home Agent did not start (tmux, desk).
data class ExternalSession(
    val agent: String,
    val threadId: String,
    val title: String,
    val cwd: String,
    val updatedAt: Double,
    val contextTokens: Long?,
    val live: Boolean,
    val liveDetail: String?
)


data class AgentSession(
    val sessionId: String,
    val title: String,
    val displayTitle: String,
    val latestTitle: String,
    val preview: String,
    val status: String,
    val startedAt: Double,
    val rootSessionId: String?,
    val agent: String,
    val reasoningEffort: String,
    val codexAccount: String,
    val codexModel: String,
    val resumeFrom: String?,
    val contextTokens: Long? = null,
    val contextWindow: Long? = null
)


data class ReasoningEffort(val value: String, val label: String)


data class CodexAccount(
    val accountId: String,
    val label: String,
    val authenticated: Boolean,
    val isDefault: Boolean
)


data class CodexModel(
    val modelId: String,
    val label: String,
    val description: String,
    val isDefault: Boolean,
    val deprecated: Boolean,
    val replacement: String?
)


data class CodexLoginSession(
    val loginSessionId: String,
    val accountId: String,
    val status: String,
    val verificationUri: String?,
    val userCode: String?,
    val output: String,
    val returncode: Int?,
    val error: String?
)


private val ReasoningOptions = listOf(
    ReasoningEffort("low", "Low"),
    ReasoningEffort("medium", "Medium"),
    ReasoningEffort("high", "High"),
    ReasoningEffort("xhigh", "XHigh")
)


@Composable
fun HomeAgentApp(launchSessionId: MutableState<String?>) {
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current
    val prefs = remember { context.getSharedPreferences("home-agent", Context.MODE_PRIVATE) }
    val client = remember {
        OkHttpClient.Builder()
            .connectTimeout(20, TimeUnit.SECONDS)
            .writeTimeout(5, TimeUnit.MINUTES)
            .readTimeout(5, TimeUnit.MINUTES)
            .callTimeout(6, TimeUnit.MINUTES)
            .build()
    }
    // Process-scoped: survives activity recreation mid-dictation.
    val recorderState = RecorderState

    var gatewayUrl by remember { mutableStateOf(prefs.getString("gateway_url", "http://192.168.10.217:8767") ?: "") }
    var token by remember { mutableStateOf(prefs.getString("token", "") ?: "") }
    var reasoningEffort by remember { mutableStateOf(prefs.getString("reasoning_effort", "medium") ?: "medium") }
    var agent by remember { mutableStateOf(prefs.getString("agent", "codex") ?: "codex") }
    var codexAccount by remember { mutableStateOf(prefs.getString("codex_account", "account1") ?: "account1") }
    var codexModel by remember { mutableStateOf(prefs.getString("codex_model", "") ?: "") }
    var claudeModel by remember { mutableStateOf(prefs.getString("claude_model", "") ?: "") }
    // "quick" = terse phone replies; "deep" = full-length planning/design answers.
    var promptMode by remember { mutableStateOf(prefs.getString("prompt_mode", "quick") ?: "quick") }
    var listenVoice by remember { mutableStateOf(prefs.getString("listen_voice", "auto") ?: "auto") }
    var contextTokens by remember { mutableStateOf<Long?>(null) }
    // Spoken-reply playback (PlaybackService owns the player; this is a remote).
    var mediaController by remember { mutableStateOf<MediaController?>(null) }
    var audioState by remember { mutableStateOf("idle") } // idle | preparing | loaded
    var audioPlaying by remember { mutableStateOf(false) }
    // The player holds one reply: the session it belongs to, and whether that
    // session has moved on to a newer turn since.
    var audioSessionId by remember { mutableStateOf<String?>(null) }
    var audioStale by remember { mutableStateOf(false) }
    val listenRequest = remember { intArrayOf(0) }
    var audioPositionMs by remember { mutableStateOf(0L) }
    var audioDurationMs by remember { mutableStateOf(0L) }
    var contextWindow by remember { mutableStateOf<Long?>(null) }
    var codexAccounts by remember { mutableStateOf<List<CodexAccount>>(emptyList()) }
    var codexModels by remember { mutableStateOf<List<CodexModel>>(emptyList()) }
    var claudeModels by remember { mutableStateOf<List<CodexModel>>(emptyList()) }
    // One composer for both a new task and a reply.
    var draft by remember { mutableStateOf("") }
    var terminal by remember { mutableStateOf("") }
    var status by remember { mutableStateOf("Ready") }
    var sessionId by remember { mutableStateOf<String?>(null) }
    var socket by remember { mutableStateOf<WebSocket?>(null) }
    var sessionRunning by remember { mutableStateOf(false) }
    var sessions by remember { mutableStateOf<List<AgentSession>>(emptyList()) }
    var showSessions by remember { mutableStateOf(false) }
    var externalSessions by remember { mutableStateOf<List<ExternalSession>>(emptyList()) }
    var showSettings by remember { mutableStateOf(false) }
    // Root of the conversation on screen; banners for it are dropped.
    var activeRootId by remember { mutableStateOf<String?>(null) }
    var showMenu by remember { mutableStateOf(false) }
    var selectedSessionId by remember { mutableStateOf<String?>(null) }
    var selectedSessionTitle by remember { mutableStateOf<String?>(null) }
    var selectedSessionAgent by remember { mutableStateOf<String?>(null) }
    var selectedSessionReasoning by remember { mutableStateOf<String?>(null) }
    var selectedSessionAccount by remember { mutableStateOf<String?>(null) }
    var selectedSessionModel by remember { mutableStateOf<String?>(null) }
    // Oldest untranscribed file in recordings/ (what "Transcribe saved" sends next).
    var lastRecordingFile by remember { mutableStateOf<File?>(null) }
    var canRetryTranscription by remember { mutableStateOf(false) }
    var pendingRecordingCount by remember { mutableStateOf(0) }
    var isTranscribing by remember { mutableStateOf(false) }
    var isSubmittingSession by remember { mutableStateOf(false) }
    var reconnectSessionId by remember { mutableStateOf<String?>(null) }
    var reconnectAttempt by remember { mutableStateOf(0) }
    var reconnectTrigger by remember { mutableStateOf(0) }
    var authDialogAccount by remember { mutableStateOf<CodexAccount?>(null) }
    var authLoginSession by remember { mutableStateOf<CodexLoginSession?>(null) }
    var authPollTrigger by remember { mutableStateOf(0) }
    var pendingAuthAccount by remember { mutableStateOf<CodexAccount?>(null) }
    // Bumped whenever the user moves to another session. Async loads and
    // starts capture it and drop their result if it changed, so a slow
    // response for the previous session can never re-attach it.
    val switchGeneration = remember { intArrayOf(0) }
    val hasCurrentSession = selectedSessionId != null || sessionId != null

    fun activeSessionId(): String? = selectedSessionId ?: sessionId

    // Resumes must stay on the agent that started the conversation; new
    // sessions use the settings toggle.
    fun activeAgent(): String = selectedSessionAgent ?: agent

    fun modelForAgent(agentId: String): String =
        if (agentId == "claude") claudeModel else codexModel

    fun resetReconnect() {
        reconnectSessionId = null
        reconnectAttempt = 0
    }

    fun requestReconnect(id: String) {
        if (activeSessionId() != id) return
        reconnectSessionId = id
        reconnectTrigger += 1
    }

    fun attachSession(id: String, title: String = id) {
        resetReconnect()
        socket?.close(1000, "switch session")
        sessionId = id
        selectedSessionId = id
        selectedSessionTitle = title
        if (selectedSessionAgent == null) selectedSessionAgent = agent
        selectedSessionReasoning = reasoningEffort
        selectedSessionAccount = codexAccount
        selectedSessionModel = modelForAgent(selectedSessionAgent ?: agent)
        sessionRunning = true
        status = "Running"
        if (activeRootId == null) activeRootId = id
        socket = connectWebSocket(client, gatewayUrl, token, id) { ws, event ->
            if (ws !== socket) {
                // Superseded connection: closing is async, so its events can still
                // arrive here. Acting on them duplicates output and orphans the
                // live socket. Close it and drop the event.
                ws.close(1000, "stale connection")
                return@connectWebSocket
            }
            when (event.optString("type")) {
                "output" -> {
                    terminal += event.optString("data")
                }
                "auth_required" -> {
                    val accountId = event.optString("account_id", selectedSessionAccount ?: codexAccount)
                    val data = event.optString("data")
                    if (data.isNotBlank()) terminal += data
                    status = "Login needed"
                    val account = codexAccounts.firstOrNull { it.accountId == accountId }
                        ?: CodexAccount(accountId, codexAccountLabel(accountId, codexAccounts), false, false)
                    authDialogAccount = account
                }
                "context" -> {
                    contextTokens = event.optLong("tokens", 0L).takeIf { it > 0L }
                    contextWindow = event.optLong("window", 0L).takeIf { it > 0L }
                }
                "status" -> {
                    val nextStatus = event.optString("status", status)
                    status = nextStatus
                    when (nextStatus) {
                        "running" -> {
                            sessionRunning = true
                            resetReconnect()
                        }
                        "waiting" -> {
                            // Claude parked on background work (background Bash,
                            // Monitor, subagent, timed wake-up). The process is
                            // alive and will wake itself; keep the session attached.
                            sessionRunning = true
                            resetReconnect()
                            val tasks = event.optJSONArray("tasks")
                            val summary = if (tasks != null && tasks.length() > 0) {
                                (0 until tasks.length()).joinToString(", ") { tasks.optString(it) }
                            } else ""
                            status = if (summary.isBlank()) "Waiting on background work" else "Waiting: $summary"
                        }
                        "idle" -> {
                            // Turn finished; the runner keeps the Claude process
                            // warm so a follow-up reuses the prompt cache. Stay
                            // attached, but let the next message go out as a resume.
                            sessionRunning = false
                            resetReconnect()
                            status = "Done - session warm"
                        }
                        "exited" -> {
                            sessionRunning = false
                            if (activeSessionId() == id) socket = null
                        }
                        "closed", "Disconnected" -> {
                            sessionRunning = false
                            if (activeSessionId() == id) {
                                socket = null
                                requestReconnect(id)
                            }
                        }
                    }
                }
            }
        }
    }

    fun loadSession(id: String) {
        resetReconnect()
        val generation = ++switchGeneration[0]
        fun stale() = generation != switchGeneration[0] || activeSessionId() != id
        if (selectedSessionId != id) activeRootId = null
        selectedSessionId = id
        sessionId = id
        status = "Loading"
        socket?.close(1000, "notification session")
        socket = null
        sessionRunning = false
        fun loadInfoAndMaybeReconnect() {
            fetchSessionInfo(client, gatewayUrl, token, id) { infoResult ->
                if (stale()) return@fetchSessionInfo
                infoResult.onSuccess { info ->
                    cancelSessionNotification(context, info.rootSessionId ?: id)
                    activeRootId = info.rootSessionId ?: id
                    selectedSessionTitle = info.displayTitle.ifBlank { info.title }
                    selectedSessionAgent = info.agent
                    selectedSessionReasoning = info.reasoningEffort
                    selectedSessionAccount = info.codexAccount
                    selectedSessionModel = info.codexModel
                    contextTokens = info.contextTokens
                    contextWindow = info.contextWindow
                    if (info.status == "running" || info.status == "waiting") {
                        attachSession(id, selectedSessionTitle ?: info.title)
                    } else {
                        status = "Done"
                    }
                }.onFailure { error ->
                    terminal += "\n[session info error] ${error.message}\n"
                }
            }
        }
        fetchSessionLog(client, gatewayUrl, token, id) { result ->
            if (stale()) return@fetchSessionLog
            result.onSuccess { history ->
                terminal = history
                loadInfoAndMaybeReconnect()
            }.onFailure { error ->
                terminal += "\n[history error] ${error.message}\n"
                status = "History load failed"
                loadInfoAndMaybeReconnect()
            }
        }
    }

    fun loadSessionFromNotification(id: String) {
        // A notification may be stale: its session id can have newer resume
        // children by the time it is tapped. Join the conversation at its
        // latest session so the user lands on the current turn.
        status = "Finding latest session"
        val generation = ++switchGeneration[0]
        listSessions(client, gatewayUrl, token) { result ->
            if (generation != switchGeneration[0]) return@listSessions
            val target = result.getOrNull()?.let { latestSessionInConversation(it, id) }
            if (target != null) {
                sessions = result.getOrDefault(sessions)
                selectedSessionTitle = target.displayTitle.ifBlank { target.title }
                selectedSessionAgent = target.agent.takeIf { value -> value.isNotBlank() }
                selectedSessionReasoning = target.reasoningEffort.takeIf { value -> value.isNotBlank() }
                selectedSessionAccount = target.codexAccount.takeIf { value -> value.isNotBlank() }
                selectedSessionModel = target.codexModel.takeIf { value -> value.isNotBlank() }
                loadSession(target.sessionId)
            } else {
                loadSession(id)
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

    fun refreshExternalSessions() {
        listExternalSessions(client, gatewayUrl, token) { result ->
            result.onSuccess {
                externalSessions = it
            }.onFailure {
                terminal += "\n[sessions error] ${it.message}\n"
            }
        }
    }

    fun refreshCodexAccounts() {
        listCodexAccounts(client, gatewayUrl, token) { result ->
            result.onSuccess { accounts ->
                codexAccounts = accounts
                val accountIds = accounts.map { it.accountId }.toSet()
                if (codexAccount !in accountIds) {
                    val next = accounts.firstOrNull { it.isDefault } ?: accounts.firstOrNull()
                    if (next != null) {
                        codexAccount = next.accountId
                        prefs.edit().putString("codex_account", next.accountId).apply()
                    }
                }
            }.onFailure {
                terminal += "\n[account warning] ${it.message}\n"
            }
        }
    }

    fun refreshCodexModels() {
        listCodexModels(client, gatewayUrl, token) { result ->
            result.onSuccess { models ->
                codexModels = models
                val modelIds = models.map { it.modelId }.toSet()
                if (codexModel.isBlank() || codexModel !in modelIds || models.any { it.modelId == codexModel && it.deprecated }) {
                    val replacement = models.firstOrNull { it.modelId == codexModel }?.replacement
                    val next = models.firstOrNull { it.modelId == replacement }
                        ?: models.firstOrNull { it.isDefault && !it.deprecated }
                        ?: models.firstOrNull { !it.deprecated }
                        ?: models.firstOrNull()
                    if (next != null) {
                        codexModel = next.modelId
                        prefs.edit().putString("codex_model", next.modelId).apply()
                    }
                }
            }.onFailure {
                terminal += "\n[model warning] ${it.message}\n"
            }
        }
    }

    fun refreshClaudeModels(force: Boolean = false) {
        listClaudeModels(client, gatewayUrl, token, force) { result ->
            result.onSuccess { models ->
                claudeModels = models
                val modelIds = models.map { it.modelId }.toSet()
                if (claudeModel.isBlank() || claudeModel !in modelIds) {
                    val next = models.firstOrNull { it.isDefault } ?: models.firstOrNull()
                    if (next != null) {
                        claudeModel = next.modelId
                        prefs.edit().putString("claude_model", next.modelId).apply()
                    }
                }
            }.onFailure {
                terminal += "\n[model warning] ${it.message}\n"
            }
        }
    }

    fun saveCodexLabel(accountId: String, label: String) {
        saveCodexAccountLabel(client, gatewayUrl, token, accountId, label) { result ->
            result.onSuccess { updated ->
                codexAccounts = codexAccounts.map {
                    if (it.accountId == updated.accountId) updated else it
                }
                refreshCodexAccounts()
            }.onFailure {
                terminal += "\n[account label warning] ${it.message}\n"
            }
        }
    }

    fun openCodexLogin(account: CodexAccount) {
        authDialogAccount = account
        authLoginSession = null
        showSettings = false
    }

    fun startCodexLogin(account: CodexAccount) {
        authDialogAccount = account
        status = "Starting login"
        startCodexAccountLogin(client, gatewayUrl, token, account.accountId) { result ->
            result.onSuccess { login ->
                authLoginSession = login
                status = if (login.status == "running") "Login pending" else "Login ${login.status}"
                authPollTrigger += 1
            }.onFailure {
                status = "Login failed"
                terminal += "\n[login error] ${it.message}\n"
            }
        }
    }

    fun refreshCodexLogin(login: CodexLoginSession) {
        getCodexAccountLogin(client, gatewayUrl, token, login.accountId, login.loginSessionId) { result ->
            result.onSuccess { updated ->
                authLoginSession = updated
                status = if (updated.status == "running") "Login pending" else "Login ${updated.status}"
                if (updated.status == "running") {
                    authPollTrigger += 1
                } else {
                    refreshCodexAccounts()
                }
            }.onFailure {
                terminal += "\n[login status error] ${it.message}\n"
            }
        }
    }

    fun cancelCodexLogin(login: CodexLoginSession?) {
        if (login != null && login.status == "running") {
            cancelCodexAccountLogin(client, gatewayUrl, token, login.accountId, login.loginSessionId) { result ->
                result.onSuccess { authLoginSession = it }
            }
        }
        authDialogAccount = null
        authLoginSession = null
        refreshCodexAccounts()
    }

    fun stopAgent() {
        resetReconnect()
        socket?.send("""{"type":"stop"}""")
        sessionId?.let { stopSession(client, gatewayUrl, token, it) }
        sessionRunning = false
        status = "Stopping agent"
    }

    fun startNewConversation() {
        resetReconnect()
        switchGeneration[0]++
        activeRootId = null
        socket?.close(1000, "new conversation")
        socket = null
        sessionId = null
        selectedSessionId = null
        selectedSessionTitle = null
        selectedSessionAgent = null
        selectedSessionReasoning = null
        selectedSessionAccount = null
        selectedSessionModel = null
        contextTokens = null
        contextWindow = null
        sessionRunning = false
        draft = ""
        terminal = ""
        status = "Ready"
    }

    fun playAudio(url: String, title: String) {
        fun start(controller: MediaController) {
            val item = MediaItem.Builder()
                .setUri(url)
                .setMediaMetadata(MediaMetadata.Builder().setTitle(title).setArtist("Home Agent").build())
                .build()
            controller.setMediaItem(item)
            controller.prepare()
            controller.play()
            audioState = "loaded"
        }
        mediaController?.let { start(it); return }
        val sessionToken = SessionToken(context, ComponentName(context, PlaybackService::class.java))
        val future = MediaController.Builder(context, sessionToken).buildAsync()
        future.addListener({
            try {
                val controller = future.get()
                mediaController = controller
                start(controller)
            } catch (error: Exception) {
                audioState = "idle"
                terminal += "\n[audio error] ${error.message}\n"
            }
        }, ContextCompat.getMainExecutor(context))
    }

    fun listenToReply() {
        val target = selectedSessionId ?: sessionId ?: return
        val request = ++listenRequest[0]
        mediaController?.pause()
        audioSessionId = target
        audioStale = false
        audioState = "preparing"
        speakSession(client, gatewayUrl, token, target, listenVoice) { result ->
            if (request != listenRequest[0]) return@speakSession  // a later Listen tap won
            result.onSuccess { (path, reason) ->
                if (reason.isNotBlank()) terminal += "\n[audio] Fast voice: $reason\n"
                playAudio("${gatewayUrl.trimEnd('/')}$path${tokenQuery(token)}", selectedSessionTitle ?: "Agent reply")
            }.onFailure {
                audioState = "idle"
                terminal += "\n[audio error] ${it.message}\n"
            }
        }
    }

    fun compactContext() {
        val target = selectedSessionId ?: sessionId ?: return
        status = "Compacting"
        val generation = switchGeneration[0]
        compactSession(client, gatewayUrl, token, target) { result ->
            if (generation != switchGeneration[0]) return@compactSession
            result.onSuccess { id ->
                terminal += "\n[compact] Compacting context on $id\n"
                attachSession(id)
            }.onFailure {
                status = "Compact failed"
                terminal += "\n[compact error] ${it.message}\n"
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
        status = "Sending"
        val resumeAgent = activeAgent()
        val generation = switchGeneration[0]
        resumeSession(client, gatewayUrl, token, target, text, resumeAgent, reasoningEffort, codexAccount, modelForAgent(resumeAgent), promptMode) { result ->
            if (generation != switchGeneration[0]) return@resumeSession
            result.onSuccess { id ->
                terminal += "\n[resume] Continuing $target as $id\n"
                attachSession(id)
            }.onFailure {
                status = "Resume failed"
                terminal += "\n[resume error] ${it.message}\n"
            }
        }
    }

    // Every .m4a in recordings/ is audio that never produced a transcript (a
    // delivered transcript deletes its file). Re-read from disk so the retry
    // offer survives process death and activity recreation.
    fun refreshPendingRecordings() {
        val pending = pendingRecordings(context)
        lastRecordingFile = pending.firstOrNull()
        pendingRecordingCount = pending.size
        canRetryTranscription = pending.isNotEmpty()
    }

    fun transcribeRecording(file: File) {
        if (!file.exists()) {
            status = "Recording unavailable"
            terminal += "\n[transcription error] Recording file missing: ${file.absolutePath}" +
                " (${pendingRecordings(context).size} other saved recording(s)).\n"
            refreshPendingRecordings()
            return
        }
        isTranscribing = true
        canRetryTranscription = false
        status = "Transcribing"
        transcribe(client, gatewayUrl, token, file) { result ->
            isTranscribing = false
            result.onSuccess {
                draft = if (draft.isBlank()) it else "${draft.trimEnd()} $it"
                // Only a delivered transcript retires the audio.
                file.delete()
                refreshPendingRecordings()
                status = if (pendingRecordingCount > 0) {
                    "Transcript ready, $pendingRecordingCount saved recording(s) left"
                } else {
                    "Transcript ready"
                }
            }.onFailure {
                status = "Transcription failed"
                refreshPendingRecordings()
                terminal += "\n[transcription error] ${it.friendlyMessage()} Recording kept: " +
                    "${describeRecording(file)}. Tap Retry transcribe.\n"
            }
        }
    }

    fun finishRecording() {
        try {
            val file = recorderState.stop(context)
            status = "Transcribing"
            transcribeRecording(file)
        } catch (error: Exception) {
            status = "Recorder failed"
            terminal += "\n[recorder error] ${error.message}\n"
            refreshPendingRecordings()
        }
    }

    fun startRecording() {
        try {
            recorderState.start(context)
            status = "Recording"
        } catch (error: Exception) {
            status = "Recorder failed"
            terminal += "\n[recorder error] ${error.message}\n"
        }
    }

    fun startNewSession(text: String) {
        if (isSubmittingSession) return
        val runAgentId = agent
        val generation = ++switchGeneration[0]
        isSubmittingSession = true
        status = "Starting ${agentLabel(runAgentId)}"
        terminal = ""
        selectedSessionAgent = runAgentId
        startSession(client, gatewayUrl, token, text, runAgentId, reasoningEffort, codexAccount, modelForAgent(runAgentId), promptMode) { result ->
            isSubmittingSession = false
            if (generation != switchGeneration[0]) return@startSession
            result.onSuccess { id ->
                draft = ""
                selectedSessionTitle = text.lineSequence().firstOrNull().orEmpty().take(80)
                attachSession(id, selectedSessionTitle ?: id)
            }.onFailure {
                status = "Start failed"
                terminal += "\n[session error] ${it.message}\n"
            }
        }
    }

    fun sendDraft() {
        val text = draft.trim()
        if (text.isBlank()) return
        if (hasCurrentSession) {
            resumeOrSend(text)
            draft = ""
        } else {
            startNewSession(text)
        }
    }

    val requestPermission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        status = if (granted) "Microphone ready" else "Microphone denied"
    }
    val requestNotificationPermission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (!granted) {
            terminal += "\n[notification warning] Notifications are disabled for Home Agent.\n"
        }
    }

    LaunchedEffect(Unit) {
        ensureNotificationChannel(context)
        pruneRecordings(context)
        refreshPendingRecordings()
        if (pendingRecordingCount > 0) {
            terminal += "\n[recording] $pendingRecordingCount saved recording(s) never got a transcript " +
                "(${pendingRecordings(context).joinToString { describeRecording(it) }}). " +
                "Tap Transcribe saved to recover them, oldest first.\n"
        }
        refreshCodexAccounts()
        refreshCodexModels()
        refreshClaudeModels()
        if (ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            requestPermission.launch(Manifest.permission.RECORD_AUDIO)
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            requestNotificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
    }

    LaunchedEffect(gatewayUrl, token) {
        if (gatewayUrl.isBlank()) return@LaunchedEffect
        registerCurrentFcmToken(context, client, gatewayUrl, token) { result ->
            result.onFailure { error ->
                terminal += "\n[notification warning] FCM registration skipped: ${error.friendlyMessage()}\n"
            }
        }
    }

    LaunchedEffect(launchSessionId.value) {
        val requestedSession = launchSessionId.value ?: return@LaunchedEffect
        launchSessionId.value = null
        loadSessionFromNotification(requestedSession)
    }

    LaunchedEffect(reconnectTrigger) {
        val target = reconnectSessionId ?: return@LaunchedEffect
        val delayMs = when (reconnectAttempt) {
            0 -> 1_000L
            1 -> 2_000L
            2 -> 5_000L
            3 -> 10_000L
            else -> 15_000L
        }
        delay(delayMs)
        if (activeSessionId() != target || reconnectSessionId != target) return@LaunchedEffect
        status = "Reconnecting"
        fetchSessionInfo(client, gatewayUrl, token, target) { result ->
            result.onSuccess { info ->
                if (activeSessionId() == target && reconnectSessionId == target) {
                    if (info.status == "running" || info.status == "waiting") {
                        fetchSessionLog(client, gatewayUrl, token, target) { historyResult ->
                            historyResult.onSuccess { terminal = it }
                            attachSession(target, info.displayTitle.ifBlank { info.title })
                        }
                    } else {
                        reconnectSessionId = null
                        sessionRunning = false
                        status = "Done"
                    }
                }
            }.onFailure { error ->
                if (activeSessionId() == target && reconnectSessionId == target) {
                    reconnectAttempt = (reconnectAttempt + 1).coerceAtMost(8)
                    reconnectTrigger += 1
                    status = "Reconnect waiting"
                    terminal += "\n[reconnect error] ${error.message}\n"
                }
            }
        }
    }

    LaunchedEffect(sessionRunning) {
        // A new turn in the session whose reply is loaded: that audio is now the old answer.
        if (sessionRunning && audioSessionId != null && audioSessionId == activeSessionId()) audioStale = true
    }

    LaunchedEffect(mediaController) {
        val controller = mediaController ?: return@LaunchedEffect
        while (true) {
            audioPlaying = controller.isPlaying
            audioPositionMs = controller.currentPosition.coerceAtLeast(0L)
            audioDurationMs = controller.duration.coerceAtLeast(0L)
            delay(500L)
        }
    }

    DisposableEffect(Unit) {
        // Releases only this remote; the service keeps playing in the background.
        onDispose { mediaController?.release() }
    }

    LaunchedEffect(authPollTrigger) {
        val login = authLoginSession ?: return@LaunchedEffect
        if (login.status != "running") return@LaunchedEffect
        delay(1_500L)
        refreshCodexLogin(login)
    }

    LaunchedEffect(pendingAuthAccount) {
        val account = pendingAuthAccount ?: return@LaunchedEffect
        pendingAuthAccount = null
        openCodexLogin(account)
    }

    // Registered once. A newly added observer immediately receives a catch-up
    // ON_RESUME, so keying this on status re-ran loadSession on every status
    // change (request storms, and a finished background session taking over).
    val reloadOnResume = rememberUpdatedState {
        val target = activeSessionId()
        // Anything posted to the tray while away is already on screen now.
        (activeRootId ?: target)?.let { cancelSessionNotification(context, it) }
        if (target != null && (!sessionRunning || status == "Disconnected" || status == "closed")) {
            loadSession(target)
        }
    }
    DisposableEffect(lifecycleOwner) {
        var catchUp = true
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) {
                if (catchUp) catchUp = false else reloadOnResume.value()
            }
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        catchUp = false
        onDispose {
            lifecycleOwner.lifecycle.removeObserver(observer)
        }
    }

    if (showSettings) {
        SettingsDialog(
            gatewayUrl = gatewayUrl,
            token = token,
            agent = agent,
            reasoningEffort = reasoningEffort,
            codexAccount = codexAccount,
            codexAccounts = codexAccounts,
            codexModel = codexModel,
            codexModels = codexModels,
            claudeModel = claudeModel,
            claudeModels = claudeModels,
            onGatewayUrl = {
                gatewayUrl = it.trim()
                prefs.edit().putString("gateway_url", gatewayUrl).apply()
                refreshCodexAccounts()
            },
            onToken = {
                token = it.trim()
                prefs.edit().putString("token", token).apply()
                refreshCodexAccounts()
            },
            onAgent = {
                agent = it
                prefs.edit().putString("agent", it).apply()
            },
            onReasoningEffort = {
                reasoningEffort = it
                prefs.edit().putString("reasoning_effort", it).apply()
            },
            onCodexAccount = {
                codexAccount = it
                prefs.edit().putString("codex_account", it).apply()
            },
            onCodexModel = {
                codexModel = it
                prefs.edit().putString("codex_model", it).apply()
            },
            onClaudeModel = {
                claudeModel = it
                prefs.edit().putString("claude_model", it).apply()
            },
            onRefreshAccounts = ::refreshCodexAccounts,
            onRefreshModels = ::refreshCodexModels,
            onRefreshClaudeModels = { refreshClaudeModels(force = true) },
            onSaveCodexAccountLabel = ::saveCodexLabel,
            onStartCodexLogin = {
                showSettings = false
                pendingAuthAccount = it
            },
            onDismiss = { showSettings = false }
        )
    }

    authDialogAccount?.let { account ->
        CodexLoginDialog(
            account = account,
            loginSession = authLoginSession,
            onStart = { startCodexLogin(account) },
            onRefresh = { authLoginSession?.let(::refreshCodexLogin) ?: startCodexLogin(account) },
            onCopyCode = { code -> copyToClipboard(context, "Codex device code", code) },
            onOpenBrowser = { uri ->
                openBrowser(context, uri) { error ->
                    terminal += "\n[login browser error] ${error.message}\n"
                }
            },
            onCancel = { cancelCodexLogin(authLoginSession) },
            onDismiss = {
                authDialogAccount = null
                authLoginSession = null
                refreshCodexAccounts()
            }
        )
    }

    val approvalPending = remember(terminal) { approvalPendingIn(terminal) }
    val allModels = codexModels + claudeModels
    // A reply is sent with the Settings model and effort, not the ones the
    // session last ran with, so describe what the next message will use.
    val nextAgent = if (hasCurrentSession) activeAgent() else agent
    val nextModel = modelForAgent(nextAgent).ifBlank { selectedSessionModel.orEmpty() }
    val nextRunLabel = listOfNotNull(
        agentLabel(nextAgent),
        nextModel.takeIf { it.isNotBlank() }?.let { codexModelLabel(it, allModels) },
        reasoningLabel(reasoningEffort)
    ).joinToString(" · ")
    val wasModel = selectedSessionModel
        ?.takeIf { hasCurrentSession && it.isNotBlank() && nextModel.isNotBlank() && it != nextModel }
        ?.let { codexModelLabel(it, allModels) }
    val contextFraction = contextTokens?.let { tokens ->
        contextWindow?.takeIf { it > 0L }?.let { (tokens.toFloat() / it).coerceIn(0f, 1f) }
    }
    val subtitle = when {
        !hasCurrentSession -> if (status == "Ready") nextRunLabel else status
        approvalPending && !sessionRunning -> "Waiting for your approval"
        else -> status
    } + (contextFraction?.let { " · context ${(it * 100).toInt()}%" } ?: "")
    val visibleAlerts = InAppAlerts.alerts.filter { alert ->
        alert.sessionId != activeSessionId() && alert.conversationId != activeRootId && alert.conversationId != activeSessionId()
    }

    LaunchedEffect(selectedSessionId, sessionId, activeRootId, InAppAlerts.alerts.size) {
        // Opening a conversation clears its banner, and a push for the
        // conversation already on screen is dropped as it arrives; otherwise it
        // sat hidden and surfaced as stale "Ready for review" after switching.
        InAppAlerts.alerts.removeAll { alert ->
            alert.sessionId == activeSessionId() || alert.conversationId == activeRootId || alert.conversationId == activeSessionId()
        }
    }

    Box(modifier = Modifier.fillMaxSize()) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .statusBarsPadding()
                .navigationBarsPadding()
                .imePadding()
                .padding(horizontal = 10.dp, vertical = 4.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp)
        ) {
            TopBar(
                title = if (hasCurrentSession) selectedSessionTitle ?: "Session" else "New chat",
                subtitle = subtitle,
                mode = promptMode,
                contextFraction = contextFraction,
                onModeChange = {
                    promptMode = it
                    prefs.edit().putString("prompt_mode", it).apply()
                },
                onSessions = {
                    showSessions = !showSessions
                    if (showSessions) {
                        refreshSessions()
                        refreshExternalSessions()
                    }
                },
                onNewChat = ::startNewConversation,
                onMenu = { showMenu = true }
            ) {
                OverflowMenu(
                    expanded = showMenu,
                    nextRunLabel = nextRunLabel + (wasModel?.let { " (was $it)" } ?: ""),
                    contextLabel = contextTokens?.let { tokens ->
                        if (contextWindow != null && contextFraction != null) {
                            "Context ${formatTokens(tokens)} / ${formatTokens(contextWindow!!)} (${(contextFraction * 100).toInt()}%)"
                        } else {
                            "Context ${formatTokens(tokens)}"
                        }
                    },
                    canCompact = activeAgent() == "claude" && hasCurrentSession && !sessionRunning,
                    onCompact = { showMenu = false; compactContext() },
                    listenVoice = listenVoice,
                    onListenVoice = {
                        listenVoice = it
                        prefs.edit().putString("listen_voice", it).apply()
                        showMenu = false
                    },
                    onSettings = { showMenu = false; showSettings = true },
                    onDismiss = { showMenu = false }
                )
            }

            visibleAlerts.lastOrNull()?.let { alert ->
                AlertBanner(
                    alert = alert,
                    more = visibleAlerts.size - 1,
                    onOpen = {
                        InAppAlerts.alerts.removeAll { it.conversationId == alert.conversationId }
                        showSessions = false
                        loadSessionFromNotification(alert.sessionId)
                    },
                    onDismiss = { InAppAlerts.alerts.removeAll { it.conversationId == alert.conversationId } }
                )
            }

            val linkHost = remember(gatewayUrl) { runCatching { Uri.parse(gatewayUrl).host }.getOrNull() }
            val replyLinks = remember(terminal, linkHost) { latestReplyLinks(terminal, linkHost) }
            CompositionLocalProvider(LocalLinkHost provides linkHost) {
                Terminal(
                    text = terminal,
                    modifier = Modifier.weight(1f)
                )
            }

            LinkBar(urls = replyLinks)

            ActionRow(
                hasSession = hasCurrentSession,
                running = sessionRunning,
                approvalPending = approvalPending,
                canRetryTranscription = canRetryTranscription && lastRecordingFile != null && !recorderState.isRecording.value,
                pendingRecordings = pendingRecordingCount,
                isTranscribing = isTranscribing,
                audioState = if (audioSessionId == activeSessionId() && !audioStale) audioState else "idle",
                audioPlaying = audioPlaying,
                otherAudioPlaying = audioPlaying && (audioSessionId != activeSessionId() || audioStale),
                audioPositionMs = audioPositionMs,
                audioDurationMs = audioDurationMs,
                canListen = hasCurrentSession && !sessionRunning && gatewayUrl.isNotBlank(),
                onApprove = { resumeOrSend("Approved. Proceed with the proposed action.") },
                onStop = ::stopAgent,
                onRetry = { lastRecordingFile?.let(::transcribeRecording) },
                onListen = ::listenToReply,
                onToggleAudio = { mediaController?.let { if (it.isPlaying) it.pause() else it.play() } },
                onBack = { mediaController?.seekBack() },
                onForward = { mediaController?.seekForward() }
            )

            Composer(
                text = draft,
                onTextChange = { draft = it },
                isRecording = recorderState.isRecording.value,
                isTranscribing = isTranscribing,
                hasSession = hasCurrentSession,
                sendLabel = when {
                    isSubmittingSession -> "Starting"
                    hasCurrentSession -> "Send"
                    else -> "Run ${agentLabel(agent)}"
                },
                sendEnabled = draft.isNotBlank() && gatewayUrl.isNotBlank() && !isSubmittingSession,
                onMic = {
                    if (!isTranscribing) {
                        if (recorderState.isRecording.value) finishRecording() else startRecording()
                    }
                },
                onSend = ::sendDraft
            )
        }

        if (showSessions) {
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .background(Color(0x66000000))
            ) {
                SessionDrawer(
                    sessions = sessions,
                    externalSessions = externalSessions,
                    onAdopt = { external ->
                        status = "Opening ${external.title.take(40)}"
                        adoptExternalSession(client, gatewayUrl, token, external) { result ->
                            result.onSuccess { session ->
                                selectedSessionTitle = session.displayTitle.ifBlank { session.title }
                                selectedSessionAgent = session.agent.takeIf { value -> value.isNotBlank() }
                                selectedSessionReasoning = null
                                selectedSessionAccount = session.codexAccount.takeIf { value -> value.isNotBlank() }
                                selectedSessionModel = null
                                loadSession(session.sessionId)
                                showSessions = false
                            }.onFailure {
                                status = "Open failed"
                                terminal += "\n[adopt error] ${it.message}\n"
                            }
                        }
                    },
                    selectedSessionId = selectedSessionId,
                    onRefresh = {
                        refreshSessions()
                        refreshExternalSessions()
                    },
                    onDismiss = { showSessions = false },
                    onSelect = { session ->
                        selectedSessionTitle = session.displayTitle.ifBlank { session.title }
                        selectedSessionAgent = session.agent.takeIf { value -> value.isNotBlank() }
                        selectedSessionReasoning = session.reasoningEffort.takeIf { value -> value.isNotBlank() }
                        selectedSessionAccount = session.codexAccount.takeIf { value -> value.isNotBlank() }
                        selectedSessionModel = session.codexModel.takeIf { value -> value.isNotBlank() }
                        loadSession(session.sessionId)
                        showSessions = false
                    }
                )
            }
        }
    }
}


@Composable
fun ReasoningSelector(
    selected: String,
    sessionReasoning: String?,
    onSelected: (String) -> Unit
) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        ReasoningOptions.chunked(2).forEach { row ->
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(6.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                row.forEach { option ->
                    val active = option.value == selected
                    val buttonColors = if (active) {
                        ButtonDefaults.buttonColors(containerColor = Primary, contentColor = Color.White)
                    } else {
                        ButtonDefaults.outlinedButtonColors(contentColor = Ink)
                    }
                    OutlinedButton(
                        onClick = { onSelected(option.value) },
                        modifier = Modifier
                            .weight(1f)
                            .height(44.dp),
                        colors = buttonColors,
                        border = BorderStroke(1.dp, if (active) Primary else Color(0xFFD7E2DD)),
                        shape = RoundedCornerShape(8.dp),
                        contentPadding = ButtonDefaults.ContentPadding
                    ) {
                        Text(option.label, maxLines = 1)
                    }
                }
                if (row.size == 1) {
                    Spacer(modifier = Modifier.weight(1f))
                }
            }
        }
        val current = sessionReasoning?.takeIf { it.isNotBlank() }
        val label = if (current == null) {
            "Reasoning for new/resumed sessions: ${reasoningLabel(selected)}"
        } else {
            "Reasoning: ${reasoningLabel(selected)}  Current session: ${reasoningLabel(current)}"
        }
        Text(
            label,
            color = Muted,
            style = MaterialTheme.typography.bodySmall
        )
    }
}


fun reasoningLabel(value: String): String {
    return ReasoningOptions.firstOrNull { it.value == value }?.label ?: value
}


fun agentLabel(value: String): String {
    return when (value) {
        "claude" -> "Claude"
        "codex" -> "Codex"
        else -> value
    }
}


fun codexAccountLabel(value: String, accounts: List<CodexAccount>): String {
    return accounts.firstOrNull { it.accountId == value }?.label ?: value
}


fun codexModelLabel(value: String, models: List<CodexModel>): String {
    return models.firstOrNull { it.modelId == value }?.label ?: value
}


@Composable
fun SettingsDialog(
    gatewayUrl: String,
    token: String,
    agent: String,
    reasoningEffort: String,
    codexAccount: String,
    codexAccounts: List<CodexAccount>,
    codexModel: String,
    codexModels: List<CodexModel>,
    claudeModel: String,
    claudeModels: List<CodexModel>,
    onGatewayUrl: (String) -> Unit,
    onToken: (String) -> Unit,
    onAgent: (String) -> Unit,
    onReasoningEffort: (String) -> Unit,
    onCodexAccount: (String) -> Unit,
    onCodexModel: (String) -> Unit,
    onClaudeModel: (String) -> Unit,
    onRefreshAccounts: () -> Unit,
    onRefreshModels: () -> Unit,
    onRefreshClaudeModels: () -> Unit,
    onSaveCodexAccountLabel: (String, String) -> Unit,
    onStartCodexLogin: (CodexAccount) -> Unit,
    onDismiss: () -> Unit
) {
    val settingsScroll = rememberScrollState()
    val maxDialogBodyHeight = (LocalConfiguration.current.screenHeightDp.dp * 0.72f).coerceAtLeast(360.dp)
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Settings") },
        text = {
            Column(
                modifier = Modifier
                    .heightIn(max = maxDialogBodyHeight)
                    .verticalScroll(settingsScroll),
                verticalArrangement = Arrangement.spacedBy(10.dp)
            ) {
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
                AgentSelector(
                    selected = agent,
                    onSelected = onAgent
                )
                ReasoningSelector(
                    selected = reasoningEffort,
                    sessionReasoning = null,
                    onSelected = onReasoningEffort
                )
                if (agent == "claude") {
                    AgentModelSelector(
                        title = "Claude model",
                        selected = claudeModel,
                        models = claudeModels,
                        fallbackModelId = "claude-fable-5",
                        onSelected = onClaudeModel,
                        onRefresh = onRefreshClaudeModels
                    )
                } else {
                    // One login (the machine's) is the normal setup now; the
                    // switcher only appears if the runner is given several again.
                    if (codexAccounts.size > 1) {
                        CodexAccountSelector(
                            selected = codexAccount,
                            accounts = codexAccounts,
                            onSelected = onCodexAccount,
                            onRefresh = onRefreshAccounts,
                            onSaveLabel = onSaveCodexAccountLabel,
                            onStartLogin = onStartCodexLogin
                        )
                    }
                    AgentModelSelector(
                        title = "Codex model",
                        selected = codexModel,
                        models = codexModels,
                        fallbackModelId = "gpt-5.5",
                        onSelected = onCodexModel,
                        onRefresh = onRefreshModels
                    )
                }
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
fun CodexAccountSelector(
    selected: String,
    accounts: List<CodexAccount>,
    onSelected: (String) -> Unit,
    onRefresh: () -> Unit,
    onSaveLabel: (String, String) -> Unit,
    onStartLogin: (CodexAccount) -> Unit
) {
    var labelDraft by remember(selected, accounts) {
        mutableStateOf(accounts.firstOrNull { it.accountId == selected }?.label ?: "")
    }
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text("Codex account", color = Ink, fontWeight = FontWeight.Medium)
            TextButton(onClick = onRefresh) {
                Text("Refresh", color = Primary)
            }
        }
        val selectedAccount = accounts.firstOrNull { it.accountId == selected }
        if (selectedAccount != null) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                OutlinedTextField(
                    value = labelDraft,
                    onValueChange = { labelDraft = it.take(80) },
                    label = { Text("Label") },
                    singleLine = true,
                    modifier = Modifier.weight(1f)
                )
                Button(
                    onClick = { onSaveLabel(selectedAccount.accountId, labelDraft.trim()) },
                    enabled = labelDraft.trim().isNotEmpty() && labelDraft.trim() != selectedAccount.label,
                    colors = ButtonDefaults.buttonColors(containerColor = Primary)
                ) {
                    Text("Save")
                }
            }
        }
        val options = if (accounts.isEmpty()) {
            listOf(CodexAccount(selected.ifBlank { "account1" }, "Account", false, true))
        } else {
            accounts
        }
        options.forEach { account ->
            val active = account.accountId == selected
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(46.dp),
                horizontalArrangement = Arrangement.spacedBy(6.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                OutlinedButton(
                    onClick = { onSelected(account.accountId) },
                    modifier = Modifier.weight(1f),
                    colors = if (active) {
                        ButtonDefaults.buttonColors(containerColor = Primary, contentColor = Color.White)
                    } else {
                        ButtonDefaults.outlinedButtonColors(contentColor = Ink)
                    },
                    border = BorderStroke(1.dp, if (active) Primary else Color(0xFFD7E2DD)),
                    shape = RoundedCornerShape(8.dp)
                ) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Text(account.label, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
                        Text(
                            if (account.authenticated) "Ready" else "Login needed",
                            style = MaterialTheme.typography.bodySmall,
                            maxLines = 1
                        )
                    }
                }
                TextButton(
                    onClick = { onStartLogin(account) },
                    modifier = Modifier.height(46.dp)
                ) {
                    Text(if (account.authenticated) "Re-auth" else "Login", color = Primary)
                }
            }
        }
    }
}


@Composable
fun AgentSelector(
    selected: String,
    onSelected: (String) -> Unit
) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text("Agent", color = Ink, fontWeight = FontWeight.Medium)
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(6.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            listOf("codex", "claude").forEach { option ->
                val active = option == selected
                OutlinedButton(
                    onClick = { onSelected(option) },
                    modifier = Modifier
                        .weight(1f)
                        .height(44.dp),
                    colors = if (active) {
                        ButtonDefaults.buttonColors(containerColor = Primary, contentColor = Color.White)
                    } else {
                        ButtonDefaults.outlinedButtonColors(contentColor = Ink)
                    },
                    border = BorderStroke(1.dp, if (active) Primary else Color(0xFFD7E2DD)),
                    shape = RoundedCornerShape(8.dp)
                ) {
                    Text(agentLabel(option), maxLines = 1)
                }
            }
        }
        Text(
            "Applies to new conversations; resumed sessions keep their agent.",
            color = Muted,
            style = MaterialTheme.typography.bodySmall
        )
    }
}


@Composable
fun AgentModelSelector(
    title: String,
    selected: String,
    models: List<CodexModel>,
    fallbackModelId: String,
    onSelected: (String) -> Unit,
    onRefresh: () -> Unit
) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text(title, color = Ink, fontWeight = FontWeight.Medium)
            TextButton(onClick = onRefresh) {
                Text("Refresh", color = Primary)
            }
        }
        val options = if (models.isEmpty()) {
            listOf(CodexModel(selected.ifBlank { fallbackModelId }, selected.ifBlank { fallbackModelId }, "", true, false, null))
        } else {
            models
        }
        options.forEach { model ->
            val active = model.modelId == selected
            val detail = when {
                model.deprecated && !model.replacement.isNullOrBlank() -> "Deprecated -> ${model.replacement}"
                model.deprecated -> "Deprecated"
                !model.replacement.isNullOrBlank() -> "Upgrade -> ${model.replacement}"
                model.isDefault -> "Default"
                else -> model.description
            }
            OutlinedButton(
                onClick = { onSelected(model.modelId) },
                modifier = Modifier
                    .fillMaxWidth()
                    .heightIn(min = 46.dp),
                colors = if (active) {
                    ButtonDefaults.buttonColors(containerColor = Primary, contentColor = Color.White)
                } else {
                    ButtonDefaults.outlinedButtonColors(contentColor = Ink)
                },
                border = BorderStroke(1.dp, if (active) Primary else Color(0xFFD7E2DD)),
                shape = RoundedCornerShape(8.dp)
            ) {
                Column(modifier = Modifier.fillMaxWidth()) {
                    Text(model.label, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    if (detail.isNotBlank()) {
                        Text(
                            detail,
                            style = MaterialTheme.typography.bodySmall,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis
                        )
                    }
                }
            }
        }
    }
}


@Composable
fun CodexLoginDialog(
    account: CodexAccount,
    loginSession: CodexLoginSession?,
    onStart: () -> Unit,
    onRefresh: () -> Unit,
    onCopyCode: (String) -> Unit,
    onOpenBrowser: (String) -> Unit,
    onCancel: () -> Unit,
    onDismiss: () -> Unit
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Codex login") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text(account.label, color = Ink, fontWeight = FontWeight.Medium)
                Text("Status: ${loginSession?.status ?: "not started"}", color = Muted)
                loginSession?.verificationUri?.let { uri ->
                    Text(
                        uri,
                        color = Primary,
                        style = MaterialTheme.typography.bodySmall,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis
                    )
                }
                loginSession?.userCode?.let { code ->
                    SelectionContainer {
                        Text(code, color = Ink, fontWeight = FontWeight.Bold)
                    }
                }
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    OutlinedButton(
                        onClick = { loginSession?.userCode?.let(onCopyCode) },
                        enabled = !loginSession?.userCode.isNullOrBlank(),
                        modifier = Modifier.weight(1f)
                    ) {
                        Text("Copy Code", maxLines = 1, overflow = TextOverflow.Ellipsis)
                    }
                    OutlinedButton(
                        onClick = { loginSession?.verificationUri?.let(onOpenBrowser) },
                        enabled = !loginSession?.verificationUri.isNullOrBlank(),
                        modifier = Modifier.weight(1f)
                    ) {
                        Text("Open Browser", maxLines = 1, overflow = TextOverflow.Ellipsis)
                    }
                }
                if (!loginSession?.error.isNullOrBlank()) {
                    Text(loginSession?.error.orEmpty(), color = Danger, style = MaterialTheme.typography.bodySmall)
                }
                if (!loginSession?.output.isNullOrBlank()) {
                    SelectionContainer {
                        Text(
                            loginSession?.output.orEmpty().trim().ifBlank { "Waiting for device code..." },
                            modifier = Modifier
                                .fillMaxWidth()
                                .widthIn(max = 340.dp)
                                .heightIn(max = 180.dp)
                                .background(Color(0xFFF1F4F0), RoundedCornerShape(6.dp))
                                .padding(8.dp),
                            color = Ink,
                            fontFamily = FontFamily.Monospace,
                            style = MaterialTheme.typography.bodySmall,
                            overflow = TextOverflow.Ellipsis
                        )
                    }
                }
            }
        },
        confirmButton = {
            Button(
                onClick = {
                    if (loginSession == null || loginSession.status != "running") onStart() else onRefresh()
                },
                colors = ButtonDefaults.buttonColors(containerColor = Primary)
            ) {
                Text(if (loginSession == null) "Start Login" else "Refresh")
            }
        },
        dismissButton = {
            TextButton(onClick = if (loginSession?.status == "running") onCancel else onDismiss) {
                Text(if (loginSession?.status == "running") "Cancel" else "Done", color = Primary)
            }
        },
        containerColor = Panel
    )
}


@Composable
fun SessionDrawer(
    sessions: List<AgentSession>,
    externalSessions: List<ExternalSession>,
    onAdopt: (ExternalSession) -> Unit,
    selectedSessionId: String?,
    onRefresh: () -> Unit,
    onDismiss: () -> Unit,
    onSelect: (AgentSession) -> Unit
) {
    val groups = remember(sessions) { groupedSessions(sessions) }
    var expandedGroups by remember(groups) { mutableStateOf<Set<String>>(emptySet()) }
    var showExternal by remember { mutableStateOf(false) }
    Card(
        modifier = Modifier
            .fillMaxHeight()
            .fillMaxWidth(0.92f)
            .statusBarsPadding()
            .navigationBarsPadding()
            .padding(vertical = 8.dp),
        shape = RoundedCornerShape(topEnd = 8.dp, bottomEnd = 8.dp),
        colors = CardDefaults.cardColors(containerColor = Panel),
        border = BorderStroke(1.dp, Color(0xFFD7E2DD))
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
                Text("Sessions", color = Ink, fontWeight = FontWeight.Bold)
                Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    TextButton(onClick = onRefresh) {
                        Text("Refresh", color = Primary)
                    }
                    TextButton(onClick = onDismiss) {
                        Text("Close", color = Muted)
                    }
                }
            }
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                listOf(false to "Home Agent", true to "This machine").forEach { (external, label) ->
                    if (showExternal == external) {
                        Button(
                            onClick = {},
                            modifier = Modifier.weight(1f),
                            colors = ButtonDefaults.buttonColors(containerColor = Primary)
                        ) { Text(label) }
                    } else {
                        OutlinedButton(onClick = { showExternal = external }, modifier = Modifier.weight(1f)) {
                            Text(label)
                        }
                    }
                }
            }
            if (showExternal) {
                if (externalSessions.isEmpty()) {
                    Text("No terminal sessions found.", color = Muted)
                } else {
                    Column(
                        modifier = Modifier
                            .fillMaxWidth()
                            .weight(1f)
                            .verticalScroll(rememberScrollState()),
                        verticalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        externalSessions.forEach { external ->
                            ExternalSessionRow(external = external, onClick = { onAdopt(external) })
                        }
                    }
                }
            } else if (sessions.isEmpty()) {
                Text("No sessions found.", color = Muted)
            } else {
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .weight(1f)
                        .verticalScroll(rememberScrollState()),
                    verticalArrangement = Arrangement.spacedBy(10.dp)
                ) {
                    groups.forEach { group ->
                        val expanded = group.rootId in expandedGroups
                        SessionGroupView(
                            group = group,
                            selectedSessionId = selectedSessionId,
                            expanded = expanded,
                            onToggle = {
                                expandedGroups = if (expanded) {
                                    expandedGroups - group.rootId
                                } else {
                                    expandedGroups + group.rootId
                                }
                            },
                            onSelect = onSelect
                        )
                    }
                }
            }
        }
    }
}


fun relativeAge(epochSeconds: Double): String {
    val minutes = ((System.currentTimeMillis() / 1000.0 - epochSeconds) / 60.0).toLong().coerceAtLeast(0L)
    return when {
        minutes < 1L -> "just now"
        minutes < 60L -> "${minutes}m ago"
        minutes < 48L * 60L -> "${minutes / 60L}h ago"
        else -> "${minutes / (60L * 24L)}d ago"
    }
}


@Composable
fun ExternalSessionRow(external: ExternalSession, onClick: () -> Unit) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick),
        shape = RoundedCornerShape(8.dp),
        colors = CardDefaults.cardColors(containerColor = Color.White),
        border = BorderStroke(1.dp, Color(0xFFD7E2DD))
    ) {
        Column(modifier = Modifier.padding(10.dp), verticalArrangement = Arrangement.spacedBy(3.dp)) {
            Text(external.title, color = Ink, fontWeight = FontWeight.Bold, maxLines = 2)
            val folder = external.cwd.removePrefix("/home/pi/").ifBlank { external.cwd }
            val context = external.contextTokens?.let { " - ${formatTokens(it)} context" }.orEmpty()
            Text(
                "${agentLabel(external.agent)} - $folder - ${relativeAge(external.updatedAt)}$context",
                color = Muted,
                style = MaterialTheme.typography.bodySmall
            )
            if (external.live) {
                Text(
                    "Open in ${external.liveDetail ?: "a terminal"} - replies here continue on a fork",
                    color = Color(0xFFB7791F),
                    style = MaterialTheme.typography.bodySmall
                )
            }
        }
    }
}


data class ConversationGroup(
    val rootId: String,
    val title: String,
    val sessions: List<AgentSession>
)


@Composable
fun SessionGroupView(
    group: ConversationGroup,
    selectedSessionId: String?,
    expanded: Boolean,
    onToggle: () -> Unit,
    onSelect: (AgentSession) -> Unit
) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        val latest = group.sessions.maxByOrNull { it.startedAt } ?: return
        val countLabel = if (group.sessions.size == 1) "1 session" else "${group.sessions.size} sessions"
        val selected = group.sessions.any { it.sessionId == selectedSessionId }
        OutlinedButton(
            onClick = {
                if (group.sessions.size == 1) {
                    onSelect(latest)
                } else {
                    onToggle()
                }
            },
            modifier = Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(8.dp),
            border = BorderStroke(1.dp, if (selected) Primary else Color(0xFFD7E2DD)),
            colors = ButtonDefaults.outlinedButtonColors(
                containerColor = if (selected) Accent else Panel,
                contentColor = Ink
            )
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(
                    if (group.sessions.size == 1) "" else if (expanded) "v" else ">",
                    color = Muted,
                    fontWeight = FontWeight.Bold,
                    modifier = Modifier.size(18.dp)
                )
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        group.title,
                        color = Ink,
                        fontWeight = FontWeight.Bold,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis
                    )
                    Text(
                        "$countLabel - ${formatSessionTime(latest.startedAt)} - ${latest.status} - ${reasoningLabel(latest.reasoningEffort)}",
                        color = Muted,
                        style = MaterialTheme.typography.bodySmall,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis
                    )
                }
            }
        }
        if (expanded && group.sessions.size > 1) {
            group.sessions.sortedByDescending { it.startedAt }.forEach { session ->
                SessionRow(session, session.sessionId == selectedSessionId, onSelect)
            }
        }
    }
}


@Composable
fun SessionRow(session: AgentSession, selected: Boolean, onSelect: (AgentSession) -> Unit) {
    val title = session.latestTitle.ifBlank { session.title.ifBlank { session.sessionId } }
    val detail = listOfNotNull(
        session.status.takeIf { it.isNotBlank() },
        formatSessionTime(session.startedAt).takeIf { it.isNotBlank() },
        session.sessionId
    ).joinToString(" - ")
    Button(
        onClick = { onSelect(session) },
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(8.dp),
        colors = ButtonDefaults.buttonColors(
            containerColor = if (selected) Primary else Accent,
            contentColor = if (selected) Color.White else Ink
        )
    ) {
        Column(
            modifier = Modifier.fillMaxWidth(),
            verticalArrangement = Arrangement.spacedBy(2.dp)
        ) {
            Text(
                title,
                fontWeight = FontWeight.Medium,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis
            )
            Text(detail, style = MaterialTheme.typography.bodySmall, maxLines = 1)
            if (session.preview.isNotBlank()) {
                Text(
                    session.preview,
                    style = MaterialTheme.typography.bodySmall,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis
                )
            }
        }
    }
}


fun groupedSessions(sessions: List<AgentSession>): List<ConversationGroup> {
    return sessions
        .groupBy { it.rootSessionId ?: it.sessionId }
        .map { (rootId, items) ->
            val root = items.firstOrNull { it.sessionId == rootId }
            val latest = items.maxByOrNull { it.startedAt }
            val titleSource = root ?: latest ?: items.first()
            ConversationGroup(
                rootId = rootId,
                title = titleSource.displayTitle.ifBlank { titleSource.title.ifBlank { rootId } },
                sessions = items
            )
        }
        .sortedByDescending { group -> group.sessions.maxOfOrNull { it.startedAt } ?: 0.0 }
}


fun latestSessionInConversation(sessions: List<AgentSession>, sessionId: String): AgentSession? {
    val known = sessions.firstOrNull { it.sessionId == sessionId }
    val rootId = known?.rootSessionId ?: known?.sessionId ?: sessionId
    return sessions
        .filter { (it.rootSessionId ?: it.sessionId) == rootId }
        .maxByOrNull { it.startedAt }
}


fun formatSessionTime(startedAt: Double): String {
    if (startedAt <= 0.0) return ""
    return SimpleDateFormat("MMM d h:mm a", Locale.US).format(Date((startedAt * 1000).toLong()))
}


@Composable
fun TopBar(
    title: String,
    subtitle: String,
    mode: String,
    contextFraction: Float?,
    onModeChange: (String) -> Unit,
    onSessions: () -> Unit,
    onNewChat: () -> Unit,
    onMenu: () -> Unit,
    menu: @Composable () -> Unit
) {
    Column {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .heightIn(min = 48.dp),
            horizontalArrangement = Arrangement.spacedBy(2.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            IconButton(onClick = onSessions) {
                Icon(painterResource(R.drawable.ic_menu_24), contentDescription = "Sessions", tint = PrimaryDark)
            }
            Column(modifier = Modifier.weight(1f)) {
                Text(title, color = Ink, fontWeight = FontWeight.Bold, maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text(subtitle, color = Muted, style = MaterialTheme.typography.bodySmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
            ModeSwitch(mode = mode, onChange = onModeChange)
            IconButton(onClick = onNewChat) {
                Icon(painterResource(R.drawable.ic_add_24), contentDescription = "New chat", tint = PrimaryDark)
            }
            Box {
                IconButton(onClick = onMenu) {
                    Icon(painterResource(R.drawable.ic_more_vert_24), contentDescription = "More", tint = PrimaryDark)
                }
                menu()
            }
        }
        // Context use as a hairline: glanceable, costs no row.
        val fraction = contextFraction ?: 0f
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .height(3.dp)
                .background(if (contextFraction == null) Color.Transparent else Color(0x1A000000), RoundedCornerShape(2.dp))
        ) {
            if (contextFraction != null) {
                Box(
                    modifier = Modifier
                        .fillMaxWidth(fraction)
                        .height(3.dp)
                        .background(contextColor(fraction), RoundedCornerShape(2.dp))
                )
            }
        }
    }
}


fun contextColor(fraction: Float): Color = when {
    fraction >= 0.8f -> Danger
    fraction >= 0.6f -> Color(0xFFB7791F)
    else -> Primary
}


// A setting, not an action: a small two-segment switch.
@Composable
fun ModeSwitch(mode: String, onChange: (String) -> Unit) {
    Row(
        modifier = Modifier
            .border(1.dp, Color(0xFFB9CCC5), RoundedCornerShape(50))
            .padding(2.dp)
    ) {
        listOf("quick" to "Quick", "deep" to "Deep").forEach { (value, label) ->
            val selected = mode == value
            Box(
                modifier = Modifier
                    .background(if (selected) Accent else Color.Transparent, RoundedCornerShape(50))
                    .clickable(enabled = !selected) { onChange(value) }
                    .padding(horizontal = 10.dp, vertical = 5.dp)
            ) {
                Text(
                    label,
                    color = if (selected) PrimaryDark else Muted,
                    fontWeight = if (selected) FontWeight.Bold else FontWeight.Normal,
                    style = MaterialTheme.typography.labelMedium
                )
            }
        }
    }
}


@Composable
fun OverflowMenu(
    expanded: Boolean,
    nextRunLabel: String,
    contextLabel: String?,
    canCompact: Boolean,
    onCompact: () -> Unit,
    listenVoice: String,
    onListenVoice: (String) -> Unit,
    onSettings: () -> Unit,
    onDismiss: () -> Unit
) {
    DropdownMenu(expanded = expanded, onDismissRequest = onDismiss) {
        Text(
            "Next message: $nextRunLabel",
            color = Muted,
            style = MaterialTheme.typography.bodySmall,
            modifier = Modifier.padding(horizontal = 16.dp, vertical = 6.dp).widthIn(max = 280.dp)
        )
        contextLabel?.let {
            Text(it, color = Muted, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp))
        }
        DropdownMenuItem(text = { Text("Compact context") }, enabled = canCompact, onClick = onCompact)
        Text("Listen voice", color = Muted, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp))
        listOf(
            "auto" to "Auto (Breeze when ready)",
            "premium" to "Breeze (may pause)",
            "fast" to "Fast (Kokoro)"
        ).forEach { (value, label) ->
            DropdownMenuItem(
                text = { Text((if (listenVoice == value) "✓ " else "    ") + label) },
                onClick = { onListenVoice(value) }
            )
        }
        DropdownMenuItem(text = { Text("Settings") }, onClick = onSettings)
    }
}


@Composable
fun AlertBanner(alert: SessionAlert, more: Int, onOpen: () -> Unit, onDismiss: () -> Unit) {
    val name = alert.sessionTitle.ifBlank { "A session" }.let { if (it.length > 60) it.take(57) + "..." else it }
    val headline = when (alert.eventType) {
        "approval_needed" -> "Needs approval: $name"
        "failed" -> "Failed: $name"
        else -> "Ready for review: $name"
    }
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(8.dp),
        colors = CardDefaults.cardColors(containerColor = Accent),
        border = BorderStroke(1.dp, Color(0xFFB9CCC5))
    ) {
        Row(
            modifier = Modifier.padding(start = 12.dp, top = 4.dp, bottom = 4.dp, end = 2.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(headline, color = Ink, fontWeight = FontWeight.Bold, style = MaterialTheme.typography.bodyMedium, maxLines = 2, overflow = TextOverflow.Ellipsis)
                val detail = listOfNotNull(
                    alert.body.takeIf { alert.eventType == "approval_needed" && it.isNotBlank() },
                    if (more > 0) "+$more more" else null
                ).joinToString(" · ")
                if (detail.isNotBlank()) {
                    Text(detail, color = Muted, style = MaterialTheme.typography.bodySmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
                }
            }
            TextButton(onClick = onOpen) { Text("Open", color = PrimaryDark, fontWeight = FontWeight.Bold) }
            TextButton(onClick = onDismiss) { Text("✕", color = Muted) }
        }
    }
}


private val SmallButtonPadding = PaddingValues(horizontal = 14.dp, vertical = 0.dp)


// Context actions above the composer; each appears only when it applies.
@Composable
fun ActionRow(
    hasSession: Boolean,
    running: Boolean,
    approvalPending: Boolean,
    canRetryTranscription: Boolean,
    pendingRecordings: Int,
    isTranscribing: Boolean,
    audioState: String,
    audioPlaying: Boolean,
    otherAudioPlaying: Boolean,
    audioPositionMs: Long,
    audioDurationMs: Long,
    canListen: Boolean,
    onApprove: () -> Unit,
    onStop: () -> Unit,
    onRetry: () -> Unit,
    onListen: () -> Unit,
    onToggleAudio: () -> Unit,
    onBack: () -> Unit,
    onForward: () -> Unit
) {
    val showApprove = hasSession && !running
    val showAudio = hasSession && (canListen || audioState != "idle" || otherAudioPlaying)
    if (!running && !showApprove && !canRetryTranscription && !showAudio) return
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .height(36.dp),
        horizontalArrangement = Arrangement.spacedBy(6.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        if (running) {
            Button(
                onClick = onStop,
                contentPadding = SmallButtonPadding,
                colors = ButtonDefaults.buttonColors(containerColor = Danger)
            ) {
                Icon(painterResource(R.drawable.ic_stop_24), contentDescription = null, modifier = Modifier.size(18.dp))
                Text(" Stop", fontWeight = FontWeight.Bold)
            }
        }
        if (showApprove) {
            if (approvalPending) {
                Button(onClick = onApprove, contentPadding = SmallButtonPadding, colors = ButtonDefaults.buttonColors(containerColor = Primary)) {
                    Text("Approve", fontWeight = FontWeight.Bold)
                }
            } else {
                OutlinedButton(onClick = onApprove, contentPadding = SmallButtonPadding) { Text("Approve") }
            }
        }
        if (canRetryTranscription) {
            OutlinedButton(onClick = onRetry, enabled = !isTranscribing, contentPadding = SmallButtonPadding) {
                Text(
                    when {
                        isTranscribing -> "Transcribing"
                        pendingRecordings > 1 -> "Transcribe saved ($pendingRecordings)"
                        else -> "Retry transcribe"
                    }
                )
            }
        }
        Spacer(modifier = Modifier.weight(1f))
        if (showAudio) {
            if (audioState == "loaded") {
                TextButton(onClick = onBack, contentPadding = PaddingValues(horizontal = 6.dp)) { Text("-15", color = Primary) }
                TextButton(onClick = onToggleAudio, contentPadding = PaddingValues(horizontal = 6.dp)) {
                    Text(if (audioPlaying) "Pause" else "Play", color = Primary, fontWeight = FontWeight.Bold)
                }
                TextButton(onClick = onForward, contentPadding = PaddingValues(horizontal = 6.dp)) { Text("+30", color = Primary) }
                Text(
                    "${formatClock(audioPositionMs)}/${formatClock(audioDurationMs)}",
                    color = Muted,
                    style = MaterialTheme.typography.labelSmall
                )
            } else {
                if (otherAudioPlaying) {
                    // Still playing another reply (another session, or this one's previous turn).
                    TextButton(onClick = onToggleAudio, contentPadding = PaddingValues(horizontal = 6.dp)) {
                        Text("Pause other", color = Primary)
                    }
                }
                OutlinedButton(onClick = onListen, enabled = canListen && audioState != "preparing", contentPadding = SmallButtonPadding) {
                    Text(if (audioState == "preparing") "Preparing..." else "Listen")
                }
            }
        }
    }
}


@Composable
fun Composer(
    text: String,
    onTextChange: (String) -> Unit,
    isRecording: Boolean,
    isTranscribing: Boolean,
    hasSession: Boolean,
    sendLabel: String,
    sendEnabled: Boolean,
    onMic: () -> Unit,
    onSend: () -> Unit
) {
    val focusManager = LocalFocusManager.current
    val keyboard = LocalSoftwareKeyboardController.current
    fun dismissKeyboard() {
        keyboard?.hide()
        focusManager.clearFocus()
    }
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(6.dp),
        verticalAlignment = Alignment.Bottom
    ) {
        Box(
            modifier = Modifier
                .padding(bottom = 4.dp)
                .size(48.dp)
                .background(if (isRecording) Danger else Accent, CircleShape)
                .clickable(enabled = !isTranscribing) { dismissKeyboard(); onMic() },
            contentAlignment = Alignment.Center
        ) {
            Icon(
                painterResource(if (isRecording) R.drawable.ic_stop_24 else R.drawable.ic_mic_24),
                contentDescription = if (isRecording) "Finish recording" else "Dictate",
                tint = if (isRecording) Color.White else PrimaryDark,
                modifier = Modifier.size(26.dp)
            )
        }
        OutlinedTextField(
            value = text,
            onValueChange = onTextChange,
            placeholder = {
                Text(
                    when {
                        isRecording -> "Recording... tap the square to finish"
                        isTranscribing -> "Transcribing..."
                        hasSession -> "Reply"
                        else -> "Describe a task"
                    },
                    color = Muted
                )
            },
            modifier = Modifier.weight(1f),
            minLines = 1,
            maxLines = 5
        )
        Button(
            onClick = { dismissKeyboard(); onSend() },
            enabled = sendEnabled,
            modifier = Modifier
                .padding(bottom = 4.dp)
                .height(48.dp),
            shape = RoundedCornerShape(24.dp),
            contentPadding = PaddingValues(horizontal = 16.dp),
            colors = ButtonDefaults.buttonColors(containerColor = Primary)
        ) {
            Text(sendLabel, fontWeight = FontWeight.Bold)
        }
    }
}


fun formatClock(ms: Long): String {
    val seconds = ms / 1000L
    return "%d:%02d".format(seconds / 60L, seconds % 60L)
}


fun formatTokens(value: Long): String =
    if (value >= 1_000_000L) String.format("%.1fM", value / 1_000_000.0) else "${value / 1000}k"


private val ApprovalMarker = Regex("""^[ \t>*_]*AWAITING_PHONE_APPROVAL:[ \t]*(?!<short action summary>)\S""", RegexOption.MULTILINE)
private val UserEchoLine = Regex("""^> """, RegexOption.MULTILINE)


// True when the newest real approval marker comes after the last echoed user
// message. The prompt echo carries the marker with its placeholder, which the
// pattern skips.
fun approvalPendingIn(text: String): Boolean {
    val marker = ApprovalMarker.findAll(text).lastOrNull() ?: return false
    val lastEcho = UserEchoLine.findAll(text).lastOrNull()?.range?.first ?: -1
    return marker.range.first > lastEcho
}


@Composable
fun Terminal(text: String, modifier: Modifier = Modifier) {
    val scrollState = rememberScrollState()
    val blocks = remember(text) { markdownBlocks(text.ifBlank { "Agent output will appear here." }) }
    val linkHost = LocalLinkHost.current
    LaunchedEffect(text.length) {
        scrollState.scrollTo(scrollState.maxValue)
    }

    Card(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(8.dp),
        colors = CardDefaults.cardColors(containerColor = Color(0xFF22312D))
    ) {
        SelectionContainer(
            modifier = Modifier
                .fillMaxSize()
                .padding(horizontal = 10.dp, vertical = 8.dp)
        ) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .verticalScroll(scrollState),
                verticalArrangement = Arrangement.spacedBy(6.dp)
            ) {
                blocks.forEach { block ->
                    MarkdownTerminalBlock(block, linkHost)
                }
            }
        }
    }
}


data class MarkdownBlock(
    val type: String,
    val text: String,
    val level: Int = 0,
    val rows: List<List<String>> = emptyList()
)

private val TableSeparator = Regex("""^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$""")

/** Split a markdown table row on unescaped pipes outside inline code. */
fun splitTableRow(line: String): List<String> {
    var body = line.trim()
    if (body.startsWith("|")) body = body.drop(1)
    if (body.endsWith("|") && !body.endsWith("\\|")) body = body.dropLast(1)
    val cells = mutableListOf<String>()
    val cell = StringBuilder()
    var inTick = false
    var i = 0
    while (i < body.length) {
        val c = body[i]
        when {
            c == '\\' && i + 1 < body.length && body[i + 1] == '|' -> { cell.append('|'); i++ }
            c == '`' -> { inTick = !inTick; cell.append(c) }
            c == '|' && !inTick -> { cells.add(cell.toString().trim()); cell.clear() }
            else -> cell.append(c)
        }
        i++
    }
    cells.add(cell.toString().trim())
    return cells
}


fun markdownBlocks(text: String): List<MarkdownBlock> {
    val blocks = mutableListOf<MarkdownBlock>()
    val paragraph = mutableListOf<String>()
    val code = mutableListOf<String>()
    val table = mutableListOf<String>()
    var inCode = false

    fun flushParagraph() {
        if (paragraph.isNotEmpty()) {
            blocks.add(MarkdownBlock("paragraph", paragraph.joinToString("\n").trimEnd()))
            paragraph.clear()
        }
    }

    fun flushCode() {
        blocks.add(MarkdownBlock("code", code.joinToString("\n").trimEnd()))
        code.clear()
    }

    fun flushTable() {
        if (table.isEmpty()) return
        if (table.size >= 2 && TableSeparator.matches(table[1].trim())) {
            flushParagraph()
            val header = splitTableRow(table[0])
            val body = table.drop(2).map { row ->
                val cells = splitTableRow(row)
                List(header.size) { cells.getOrElse(it) { "" } }
            }
            blocks.add(MarkdownBlock("table", "", rows = listOf(header) + body))
        } else {
            paragraph.addAll(table)
        }
        table.clear()
    }

    text.lines().forEach { line ->
        val trimmed = line.trimEnd()
        if (!inCode) {
            val t = trimmed.trimStart()
            val isTableLine = t.startsWith("|") && t.count { it == '|' } >= 2 ||
                (table.size == 1 && TableSeparator.matches(t))
            if (isTableLine) {
                table.add(t)
                return@forEach
            }
            flushTable()
        }
        if (trimmed.trimStart().startsWith("```")) {
            if (inCode) {
                flushCode()
                inCode = false
            } else {
                flushParagraph()
                inCode = true
            }
            return@forEach
        }
        if (inCode) {
            code.add(line)
            return@forEach
        }
        if (trimmed.isBlank()) {
            flushParagraph()
            return@forEach
        }
        val heading = Regex("^(#{1,4})\\s+(.+)$").matchEntire(trimmed)
        if (heading != null) {
            flushParagraph()
            blocks.add(MarkdownBlock("heading", heading.groupValues[2], heading.groupValues[1].length))
            return@forEach
        }
        if (trimmed.startsWith("- ") || trimmed.startsWith("* ")) {
            flushParagraph()
            blocks.add(MarkdownBlock("bullet", trimmed.drop(2).trim()))
            return@forEach
        }
        if (trimmed.startsWith("$ ") || trimmed.startsWith("[") || trimmed.contains("AWAITING_PHONE_APPROVAL:")) {
            flushParagraph()
            blocks.add(MarkdownBlock("terminal", trimmed))
            return@forEach
        }
        paragraph.add(trimmed)
    }
    if (inCode) flushCode()
    flushTable()
    flushParagraph()
    return blocks
}


@Composable
fun MarkdownTerminalBlock(block: MarkdownBlock, linkHost: String? = null) {
    when (block.type) {
        "heading" -> Text(
            text = block.text,
            color = Color(0xFFF4F2ED),
            fontWeight = FontWeight.Bold,
            style = if (block.level <= 2) MaterialTheme.typography.titleMedium else MaterialTheme.typography.bodyLarge
        )
        "bullet" -> Text(
            text = terminalInlineAnnotated("- ${block.text}", terminalLineStyle(block.text), linkHost),
            style = MaterialTheme.typography.bodyMedium
        )
        "code" -> Text(
            text = linkifiedString(block.text.ifBlank { " " }, linkHost),
            modifier = Modifier
                .fillMaxWidth()
                .background(Color(0xFF17221F), RoundedCornerShape(6.dp))
                .padding(8.dp),
            color = Color(0xFFE6EFE9),
            fontFamily = FontFamily.Monospace,
            style = MaterialTheme.typography.bodySmall
        )
        "table" -> MarkdownTable(block.rows, linkHost)
        "terminal" -> Text(
            text = terminalInlineAnnotated(block.text, terminalLineStyle(block.text), linkHost),
            fontFamily = FontFamily.Monospace,
            style = MaterialTheme.typography.bodySmall
        )
        else -> Text(
            text = terminalInlineAnnotated(block.text, SpanStyle(color = Color(0xFFF4F2ED)), linkHost),
            style = MaterialTheme.typography.bodyMedium
        )
    }
}


/** Rough rendered length of a cell: markdown syntax stripped, link URLs dropped. */
private fun cellDisplayLength(cell: String): Int =
    cell.replace(Regex("\\[([^\\]]+)]\\([^)]+\\)"), "$1").replace("**", "").replace("`", "").length


/**
 * Markdown tables on a phone: a striped grid when the columns fit the screen,
 * otherwise one card per row (first cell as the title, the rest as
 * "Header  value" lines) so nothing needs sideways scrolling.
 */
@Composable
fun MarkdownTable(rows: List<List<String>>, linkHost: String? = null) {
    if (rows.isEmpty()) return
    val header = rows.first()
    val body = rows.drop(1)
    val cols = header.size
    val text = Color(0xFFF4F2ED)
    val dim = Color(0xFF9FB3AA)
    val border = Color(0xFF2C3A35)
    // Widest cell per column, capped so one long cell does not starve the rest.
    val natural = (0 until cols).map { c ->
        rows.maxOf { cellDisplayLength(it.getOrElse(c) { "" }) }.coerceIn(3, 28)
    }
    BoxWithConstraints(Modifier.fillMaxWidth()) {
        val charDp = 7.6f
        val cellPadDp = 12f
        val needed = natural.sumOf { (it * charDp + cellPadDp).toDouble() }
        if (cols <= 1 || needed <= maxWidth.value || body.isEmpty()) {
            Column(
                Modifier
                    .fillMaxWidth()
                    .border(1.dp, border, RoundedCornerShape(6.dp))
                    .clip(RoundedCornerShape(6.dp))
            ) {
                rows.forEachIndexed { r, row ->
                    val bg = when {
                        r == 0 -> Color(0xFF1F2E29)
                        r % 2 == 0 -> Color(0xFF141C19)
                        else -> Color.Transparent
                    }
                    Row(Modifier.fillMaxWidth().background(bg)) {
                        row.forEachIndexed { c, cell ->
                            Text(
                                text = terminalInlineAnnotated(
                                    cell,
                                    SpanStyle(
                                        color = text,
                                        fontWeight = if (r == 0) FontWeight.Bold else null
                                    ),
                                    linkHost
                                ),
                                modifier = Modifier
                                    .weight(natural[c].toFloat())
                                    .padding(horizontal = 6.dp, vertical = 5.dp),
                                style = MaterialTheme.typography.bodySmall
                            )
                        }
                    }
                }
            }
        } else {
            Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                body.forEach { row ->
                    Column(
                        Modifier
                            .fillMaxWidth()
                            .background(Color(0xFF141C19), RoundedCornerShape(6.dp))
                            .border(1.dp, border, RoundedCornerShape(6.dp))
                            .padding(horizontal = 10.dp, vertical = 7.dp),
                        verticalArrangement = Arrangement.spacedBy(2.dp)
                    ) {
                        Text(
                            text = terminalInlineAnnotated(
                                row.first().ifBlank { "—" },
                                SpanStyle(color = text, fontWeight = FontWeight.Bold),
                                linkHost
                            ),
                            style = MaterialTheme.typography.bodyMedium
                        )
                        row.drop(1).forEachIndexed { i, cell ->
                            if (cell.isBlank()) return@forEachIndexed
                            Row {
                                Text(
                                    text = header.getOrElse(i + 1) { "" },
                                    color = dim,
                                    modifier = Modifier.widthIn(min = 72.dp, max = 120.dp).padding(end = 8.dp),
                                    style = MaterialTheme.typography.bodySmall
                                )
                                Text(
                                    text = terminalInlineAnnotated(cell, SpanStyle(color = text), linkHost),
                                    modifier = Modifier.weight(1f),
                                    style = MaterialTheme.typography.bodySmall
                                )
                            }
                        }
                    }
                }
            }
        }
    }
}


fun terminalInlineAnnotated(text: String, baseStyle: SpanStyle, linkHost: String? = null) = buildAnnotatedString {
    pushStyle(baseStyle)
    // Markdown links first so their URL is not also matched as a bare link.
    val pattern = Regex("(\\[[^\\]\\n]+]\\([^)\\s]+\\)|`[^`]+`|\\*\\*[^*]+\\*\\*)")
    var index = 0
    pattern.findAll(text).forEach { match ->
        appendLinkified(text.substring(index, match.range.first), linkHost)
        val token = match.value
        if (token.startsWith("[")) {
            val label = token.substring(1, token.indexOf("]("))
            val url = openableUrl(trimUrl(token.substring(token.indexOf("](") + 2, token.length - 1)), linkHost)
            withLink(linkAnnotation(url)) { append(label) }
        } else if (token.startsWith("`")) {
            pushStyle(SpanStyle(color = Color(0xFFE6EFE9), background = Color(0xFF17221F), fontFamily = FontFamily.Monospace))
            appendLinkified(token.trim('`'), linkHost)
            pop()
        } else {
            pushStyle(SpanStyle(fontWeight = FontWeight.Bold))
            appendLinkified(token.removePrefix("**").removeSuffix("**"), linkHost)
            pop()
        }
        index = match.range.last + 1
    }
    appendLinkified(text.substring(index), linkHost)
    pop()
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
            trimmed.startsWith("[claude failed]") ||
            trimmed.startsWith("[tool error]") ||
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
            trimmed.startsWith("[claude]") ||
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


// Process-scoped (an `object`, not `remember`): an activity recreated mid-dictation
// re-attaches to the live recorder instead of leaking it and showing the mic idle.
object RecorderState {
    val isRecording: MutableState<Boolean> = mutableStateOf(false)
    private var recorder: MediaRecorder? = null
    private var outputFile: File? = null

    fun currentFile(): File? = outputFile

    fun start(context: Context) {
        if (isRecording.value) return
        // filesDir, never cacheDir: Android wipes app caches when storage runs
        // low (it did on 2026-10-18 with the phone at 100%) and took two long
        // dictations with it.
        val stamp = SimpleDateFormat("yyyyMMdd-HHmmss", Locale.US).format(Date())
        val file = File(recordingsDir(context), "dictation-$stamp.m4a")
        outputFile = file
        val service = Intent(context, RecordingService::class.java)
        try {
            ContextCompat.startForegroundService(context, service)
        } catch (error: Exception) {
            android.util.Log.w("HomeAgent", "Recording foreground service unavailable: ${error.message}")
        }
        try {
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
        } catch (error: Exception) {
            recorder?.let { runCatching { it.release() } }
            recorder = null
            outputFile = null
            file.delete()
            context.stopService(service)
            throw error
        }
        isRecording.value = true
    }

    // Always hands back (or keeps on disk) whatever was captured: a failing
    // MediaRecorder.stop() surfaces as an exception that names the kept file.
    fun stop(context: Context): File {
        val file = outputFile ?: error("No recording in progress")
        val live = recorder
        recorder = null
        outputFile = null
        isRecording.value = false
        context.stopService(Intent(context, RecordingService::class.java))
        var failure: RuntimeException? = null
        try {
            live?.stop()
        } catch (error: RuntimeException) {
            failure = error
        }
        runCatching { live?.release() }
        if (failure != null) {
            throw IllegalStateException(
                "MediaRecorder.stop failed (${failure.message}); kept ${describeRecording(file)}",
                failure
            )
        }
        return file
    }
}


fun recordingsDir(context: Context): File = File(context.filesDir, "recordings").apply { mkdirs() }

fun describeRecording(file: File): String = "${file.name} (${file.length() / 1024} KB)"

// Untranscribed recordings, oldest first (names are timestamps). The one being
// recorded right now is excluded.
fun pendingRecordings(context: Context): List<File> {
    val current = RecorderState.currentFile()
    return recordingsDir(context)
        .listFiles { candidate -> candidate.isFile && candidate.name.endsWith(".m4a") }
        .orEmpty()
        .filter { it != current }
        .sortedBy { it.name }
}

// Failed-transcript audio is kept for a week, then dropped.
fun pruneRecordings(context: Context) {
    val cutoff = System.currentTimeMillis() - 7L * 24 * 60 * 60 * 1000
    pendingRecordings(context).filter { it.lastModified() < cutoff }.forEach { it.delete() }
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
    agent: String,
    reasoningEffort: String,
    codexAccount: String,
    codexModel: String,
    mode: String,
    callback: (Result<String>) -> Unit
) {
    val json = JSONObject(
        mapOf(
            "mode" to mode,
            "text" to transcript,
            "agent" to agent,
            "reasoning_effort" to reasoningEffort,
            "codex_account" to codexAccount,
            "codex_model" to codexModel
        )
    ).toString()
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
    agent: String,
    reasoningEffort: String,
    codexAccount: String,
    codexModel: String,
    mode: String,
    callback: (Result<String>) -> Unit
) {
    val json = JSONObject(
        mapOf(
            "mode" to mode,
            "text" to text,
            "agent" to agent,
            "reasoning_effort" to reasoningEffort,
            "codex_account" to codexAccount,
            "codex_model" to codexModel
        )
    ).toString()
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
            parseAgentSession(array.getJSONObject(index))
        }
    })
}


fun listExternalSessions(
    client: OkHttpClient,
    gatewayUrl: String,
    token: String,
    callback: (Result<List<ExternalSession>>) -> Unit
) {
    val request = Request.Builder()
        .url("${gatewayUrl.trimEnd('/')}/api/external/sessions${tokenQuery(token)}")
        .get()
        .build()
    client.newCall(request).enqueue(resultCallback(callback) { response ->
        val array = JSONArray(response.body?.string().orEmpty())
        (0 until array.length()).map { index ->
            val item = array.getJSONObject(index)
            ExternalSession(
                agent = item.optString("agent", "claude"),
                threadId = item.getString("thread_id"),
                title = item.optString("title", item.getString("thread_id")),
                cwd = item.optString("cwd", ""),
                updatedAt = item.optDouble("updated_at", 0.0),
                contextTokens = item.optLong("context_tokens", 0L).takeIf { it > 0L },
                live = item.optBoolean("live", false),
                liveDetail = item.optString("live_detail").takeIf { it.isNotBlank() && it != "null" }
            )
        }
    })
}


fun adoptExternalSession(
    client: OkHttpClient,
    gatewayUrl: String,
    token: String,
    external: ExternalSession,
    callback: (Result<AgentSession>) -> Unit
) {
    val json = JSONObject(mapOf("agent" to external.agent, "thread_id" to external.threadId)).toString()
    val request = Request.Builder()
        .url("${gatewayUrl.trimEnd('/')}/api/external/adopt${tokenQuery(token)}")
        .post(json.toRequestBody("application/json".toMediaType()))
        .build()
    client.newCall(request).enqueue(resultCallback(callback) { response ->
        parseAgentSession(JSONObject(response.body?.string().orEmpty()))
    })
}


fun listCodexAccounts(
    client: OkHttpClient,
    gatewayUrl: String,
    token: String,
    callback: (Result<List<CodexAccount>>) -> Unit
) {
    if (gatewayUrl.isBlank()) {
        callback(Result.success(emptyList()))
        return
    }
    val request = Request.Builder()
        .url("${gatewayUrl.trimEnd('/')}/api/codex/accounts${tokenQuery(token)}")
        .get()
        .build()
    client.newCall(request).enqueue(resultCallback(callback) { response ->
        val array = JSONArray(response.body?.string().orEmpty())
        (0 until array.length()).map { index ->
            parseCodexAccount(array.getJSONObject(index))
        }
    })
}


fun listCodexModels(
    client: OkHttpClient,
    gatewayUrl: String,
    token: String,
    callback: (Result<List<CodexModel>>) -> Unit
) {
    if (gatewayUrl.isBlank()) {
        callback(Result.success(emptyList()))
        return
    }
    val request = Request.Builder()
        .url("${gatewayUrl.trimEnd('/')}/api/codex/models${tokenQuery(token)}")
        .get()
        .build()
    client.newCall(request).enqueue(resultCallback(callback) { response ->
        val array = JSONArray(response.body?.string().orEmpty())
        (0 until array.length()).map { index ->
            parseCodexModel(array.getJSONObject(index))
        }
    })
}


fun listClaudeModels(
    client: OkHttpClient,
    gatewayUrl: String,
    token: String,
    force: Boolean = false,
    callback: (Result<List<CodexModel>>) -> Unit
) {
    if (gatewayUrl.isBlank()) {
        callback(Result.success(emptyList()))
        return
    }
    val request = Request.Builder()
        .url(
            "${gatewayUrl.trimEnd('/')}/api/claude/models${tokenQuery(token)}" +
                if (force) (if (token.isBlank()) "?refresh=true" else "&refresh=true") else ""
        )
        .get()
        .build()
    client.newCall(request).enqueue(resultCallback(callback) { response ->
        val array = JSONArray(response.body?.string().orEmpty())
        (0 until array.length()).map { index ->
            parseCodexModel(array.getJSONObject(index))
        }
    })
}


fun saveCodexAccountLabel(
    client: OkHttpClient,
    gatewayUrl: String,
    token: String,
    accountId: String,
    label: String,
    callback: (Result<CodexAccount>) -> Unit
) {
    if (gatewayUrl.isBlank() || accountId.isBlank() || label.isBlank()) {
        callback(Result.failure(IllegalArgumentException("account label is required")))
        return
    }
    val json = JSONObject(mapOf("label" to label)).toString()
    val request = Request.Builder()
        .url("${gatewayUrl.trimEnd('/')}/api/codex/accounts/$accountId/label${tokenQuery(token)}")
        .post(json.toRequestBody("application/json".toMediaType()))
        .build()
    client.newCall(request).enqueue(resultCallback(callback) { response ->
        parseCodexAccount(JSONObject(response.body?.string().orEmpty()))
    })
}


fun startCodexAccountLogin(
    client: OkHttpClient,
    gatewayUrl: String,
    token: String,
    accountId: String,
    callback: (Result<CodexLoginSession>) -> Unit
) {
    if (gatewayUrl.isBlank() || accountId.isBlank()) {
        callback(Result.failure(IllegalArgumentException("account is required")))
        return
    }
    val request = Request.Builder()
        .url("${gatewayUrl.trimEnd('/')}/api/codex/accounts/$accountId/login${tokenQuery(token)}")
        .post(ByteArray(0).toRequestBody(null))
        .build()
    client.newCall(request).enqueue(resultCallback(callback) { response ->
        parseCodexLoginSession(JSONObject(response.body?.string().orEmpty()))
    })
}


fun getCodexAccountLogin(
    client: OkHttpClient,
    gatewayUrl: String,
    token: String,
    accountId: String,
    loginSessionId: String,
    callback: (Result<CodexLoginSession>) -> Unit
) {
    val request = Request.Builder()
        .url("${gatewayUrl.trimEnd('/')}/api/codex/accounts/$accountId/login/$loginSessionId${tokenQuery(token)}")
        .get()
        .build()
    client.newCall(request).enqueue(resultCallback(callback) { response ->
        parseCodexLoginSession(JSONObject(response.body?.string().orEmpty()))
    })
}


fun cancelCodexAccountLogin(
    client: OkHttpClient,
    gatewayUrl: String,
    token: String,
    accountId: String,
    loginSessionId: String,
    callback: (Result<CodexLoginSession>) -> Unit
) {
    val request = Request.Builder()
        .url("${gatewayUrl.trimEnd('/')}/api/codex/accounts/$accountId/login/$loginSessionId/cancel${tokenQuery(token)}")
        .post(ByteArray(0).toRequestBody(null))
        .build()
    client.newCall(request).enqueue(resultCallback(callback) { response ->
        parseCodexLoginSession(JSONObject(response.body?.string().orEmpty()))
    })
}


fun parseCodexAccount(item: JSONObject): CodexAccount {
    val accountId = item.optString("account_id", "")
    return CodexAccount(
        accountId = accountId,
        label = item.optString("label", accountId.ifBlank { "Account" }),
        authenticated = item.optBoolean("authenticated", false),
        isDefault = item.optBoolean("is_default", false)
    )
}


fun parseCodexModel(item: JSONObject): CodexModel {
    val modelId = item.optString("model_id", "")
    return CodexModel(
        modelId = modelId,
        label = item.optString("label", modelId.ifBlank { "Model" }),
        description = item.optString("description", ""),
        isDefault = item.optBoolean("is_default", false),
        deprecated = item.optBoolean("deprecated", false),
        replacement = item.optString("replacement").takeIf { it.isNotBlank() && it != "null" }
    )
}


fun parseCodexLoginSession(item: JSONObject): CodexLoginSession {
    return CodexLoginSession(
        loginSessionId = item.optString("login_session_id", ""),
        accountId = item.optString("account_id", ""),
        status = item.optString("status", "unknown"),
        verificationUri = item.optString("verification_uri").takeIf { it.isNotBlank() && it != "null" },
        userCode = item.optString("user_code").takeIf { it.isNotBlank() && it != "null" },
        output = item.optString("output", ""),
        returncode = if (item.has("returncode") && !item.isNull("returncode")) item.optInt("returncode") else null,
        error = item.optString("error").takeIf { it.isNotBlank() && it != "null" }
    )
}


fun fetchSessionInfo(
    client: OkHttpClient,
    gatewayUrl: String,
    token: String,
    sessionId: String,
    callback: (Result<AgentSession>) -> Unit
) {
    val request = Request.Builder()
        .url("${gatewayUrl.trimEnd('/')}/api/sessions/$sessionId${tokenQuery(token)}")
        .get()
        .build()
    client.newCall(request).enqueue(resultCallback(callback) { response ->
        parseAgentSession(JSONObject(response.body?.string().orEmpty()))
    })
}


fun parseAgentSession(item: JSONObject): AgentSession {
    val sessionId = item.getString("session_id")
    return AgentSession(
        sessionId = sessionId,
        title = item.optString("title", sessionId),
        displayTitle = item.optString("display_title", item.optString("title", sessionId)),
        latestTitle = item.optString("latest_title", item.optString("title", "")),
        preview = item.optString("preview", ""),
        status = item.optString("status", "archived"),
        startedAt = item.optDouble("started_at", 0.0),
        rootSessionId = item.optString("root_session_id").takeIf { it.isNotBlank() && it != "null" },
        agent = item.optString("agent", "codex").ifBlank { "codex" },
        reasoningEffort = item.optString("reasoning_effort", "medium"),
        codexAccount = item.optString("codex_account", "account1"),
        codexModel = item.optString("codex_model", ""),
        resumeFrom = item.optString("resume_from").takeIf { it.isNotBlank() && it != "null" },
        contextTokens = item.optLong("context_tokens", 0L).takeIf { it > 0L },
        contextWindow = item.optLong("context_window", 0L).takeIf { it > 0L }
    )
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


fun speakSession(
    client: OkHttpClient,
    gatewayUrl: String,
    token: String,
    sessionId: String,
    voice: String,
    callback: (Result<Pair<String, String>>) -> Unit
) {
    // Rendering a long reply takes a while; don't inherit the short API timeout.
    val patient = client.newBuilder().readTimeout(300, java.util.concurrent.TimeUnit.SECONDS).build()
    val request = Request.Builder()
        .url("${gatewayUrl.trimEnd('/')}/api/sessions/$sessionId/speak${tokenQuery(token)}".let {
            it + (if ('?' in it) "&" else "?") + "voice=$voice"
        })
        .post(ByteArray(0).toRequestBody(null))
        .build()
    patient.newCall(request).enqueue(resultCallback(callback) { response ->
        val body = JSONObject(response.body?.string().orEmpty())
        body.getString("audio") to (if (body.isNull("reason")) "" else body.optString("reason"))
    })
}


fun compactSession(
    client: OkHttpClient,
    gatewayUrl: String,
    token: String,
    sessionId: String,
    callback: (Result<String>) -> Unit
) {
    val request = Request.Builder()
        .url("${gatewayUrl.trimEnd('/')}/api/sessions/$sessionId/compact${tokenQuery(token)}")
        .post(ByteArray(0).toRequestBody(null))
        .build()
    client.newCall(request).enqueue(resultCallback(callback) { response ->
        JSONObject(response.body?.string().orEmpty()).getString("session_id")
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


fun registerCurrentFcmToken(
    context: Context,
    client: OkHttpClient,
    gatewayUrl: String,
    token: String,
    callback: (Result<Unit>) -> Unit = {}
) {
    if (gatewayUrl.isBlank()) {
        callback(Result.success(Unit))
        return
    }
    try {
        FirebaseMessaging.getInstance().token.addOnCompleteListener { task ->
            if (!task.isSuccessful) {
                callback(Result.failure(task.exception ?: IOException("FCM token unavailable")))
                return@addOnCompleteListener
            }
            val fcmToken = task.result.orEmpty()
            if (fcmToken.isBlank()) {
                callback(Result.failure(IOException("FCM token unavailable")))
                return@addOnCompleteListener
            }
            context.getSharedPreferences("home-agent", Context.MODE_PRIVATE)
                .edit()
                .putString("fcm_token", fcmToken)
                .apply()
            registerDeviceToken(client, gatewayUrl, token, fcmToken, callback)
        }
    } catch (error: Throwable) {
        callback(Result.failure(error))
    }
}


fun registerDeviceToken(
    client: OkHttpClient,
    gatewayUrl: String,
    token: String,
    fcmToken: String,
    callback: (Result<Unit>) -> Unit = {}
) {
    if (gatewayUrl.isBlank() || fcmToken.isBlank()) {
        callback(Result.success(Unit))
        return
    }
    val json = JSONObject(
        mapOf(
            "fcm_token" to fcmToken,
            "platform" to "android",
            "device_label" to deviceLabel()
        )
    ).toString()
    val request = Request.Builder()
        .url("${gatewayUrl.trimEnd('/')}/api/devices/register${tokenQuery(token)}")
        .post(json.toRequestBody("application/json".toMediaType()))
        .build()
    client.newCall(request).enqueue(resultCallback(callback) { response ->
        response.body?.string()
        Unit
    })
}


fun unregisterDeviceToken(
    client: OkHttpClient,
    gatewayUrl: String,
    token: String,
    fcmToken: String,
    callback: (Result<Unit>) -> Unit = {}
) {
    if (gatewayUrl.isBlank() || fcmToken.isBlank()) {
        callback(Result.success(Unit))
        return
    }
    val json = JSONObject(mapOf("fcm_token" to fcmToken)).toString()
    val request = Request.Builder()
        .url("${gatewayUrl.trimEnd('/')}/api/devices/unregister${tokenQuery(token)}")
        .post(json.toRequestBody("application/json".toMediaType()))
        .build()
    client.newCall(request).enqueue(resultCallback(callback) { response ->
        response.body?.string()
        Unit
    })
}


fun deviceLabel(): String {
    return listOf(Build.MANUFACTURER, Build.MODEL)
        .joinToString(" ")
        .trim()
        .ifBlank { "Android device" }
}


fun copyToClipboard(context: Context, label: String, text: String) {
    val manager = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
    manager.setPrimaryClip(ClipData.newPlainText(label, text))
}


fun openBrowser(context: Context, uri: String, onError: (Throwable) -> Unit) {
    try {
        context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(uri)))
    } catch (error: ActivityNotFoundException) {
        onError(error)
    }
}


fun ensureNotificationChannel(context: Context) {
    if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
    val manager = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
    val channel = NotificationChannel(
        NOTIFICATION_CHANNEL_ID,
        "Home Agent sessions",
        NotificationManager.IMPORTANCE_DEFAULT
    ).apply {
        description = "Alerts when a disconnected Home Agent session needs attention."
    }
    manager.createNotificationChannel(channel)
}


fun postSessionNotification(
    context: Context,
    sessionId: String,
    conversationId: String,
    title: String,
    message: String
) {
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
        ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
    ) {
        return
    }
    ensureNotificationChannel(context)
    val intent = Intent(context, MainActivity::class.java).apply {
        flags = Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP
        putExtra(EXTRA_SESSION_ID, sessionId)
    }
    // Keyed by conversation (root session), not session id: each resume creates a
    // new session id, so per-session ids stacked one stale notification per turn.
    val pendingIntent = PendingIntent.getActivity(
        context,
        conversationId.hashCode(),
        intent,
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
    )
    val notification = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
        android.app.Notification.Builder(context, NOTIFICATION_CHANNEL_ID)
    } else {
        @Suppress("DEPRECATION")
        android.app.Notification.Builder(context)
    }
        .setSmallIcon(R.drawable.ic_settings_24)
        .setContentTitle(title)
        .setContentText(message)
        .setContentIntent(pendingIntent)
        .setAutoCancel(true)
        .build()
    val manager = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
    manager.notify(SESSION_NOTIFICATION_ID + conversationId.hashCode(), notification)
}


fun cancelSessionNotification(context: Context, conversationId: String) {
    val manager = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
    manager.cancel(SESSION_NOTIFICATION_ID + conversationId.hashCode())
}


fun connectWebSocket(
    client: OkHttpClient,
    gatewayUrl: String,
    token: String,
    sessionId: String,
    onEvent: (WebSocket, JSONObject) -> Unit
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
                try {
                    onEvent(webSocket, JSONObject(text))
                } catch (error: JSONException) {
                    onEvent(
                        webSocket,
                        JSONObject(
                            mapOf(
                                "type" to "output",
                                "data" to "\n[websocket error] invalid gateway message: ${error.message}\n"
                            )
                        )
                    )
                }
            }
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            onMainThread {
                onEvent(webSocket, JSONObject(mapOf("type" to "output", "data" to "\n[websocket error] ${t.message}\n")))
                onEvent(webSocket, JSONObject(mapOf("type" to "status", "status" to "Disconnected")))
            }
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            onMainThread {
                onEvent(webSocket, JSONObject(mapOf("type" to "status", "status" to "closed")))
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
                    val errorText = it.body?.string().orEmpty()
                    onMainThread { callback(Result.failure(IOException(errorText))) }
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


fun Throwable.friendlyMessage(): String {
    val message = message.orEmpty()
    return when {
        this is java.net.SocketTimeoutException || message.contains("timeout", ignoreCase = true) ->
            "Transcription timed out. Tap Retry Transcribe Last Recording to send the saved audio again."
        message.isNotBlank() -> message
        else -> this::class.java.simpleName
    }
}
