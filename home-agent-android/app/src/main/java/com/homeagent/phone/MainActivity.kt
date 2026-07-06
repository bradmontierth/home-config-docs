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
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
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
import androidx.compose.runtime.DisposableEffect
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
        launchSessionId.value = intent.homeAgentSessionId()
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
                    HomeAgentApp(launchSessionId)
                }
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        launchSessionId.value = intent.homeAgentSessionId()
    }
}


private fun Intent.homeAgentSessionId(): String? {
    return getStringExtra(EXTRA_SESSION_ID) ?: getStringExtra(FCM_DATA_SESSION_ID)
}


data class AgentSession(
    val sessionId: String,
    val title: String,
    val displayTitle: String,
    val latestTitle: String,
    val preview: String,
    val status: String,
    val startedAt: Double,
    val rootSessionId: String?,
    val reasoningEffort: String,
    val codexAccount: String,
    val codexModel: String,
    val resumeFrom: String?
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
    val recorderState = remember { RecorderState(context) }

    var gatewayUrl by remember { mutableStateOf(prefs.getString("gateway_url", "http://192.168.10.217:8767") ?: "") }
    var token by remember { mutableStateOf(prefs.getString("token", "") ?: "") }
    var reasoningEffort by remember { mutableStateOf(prefs.getString("reasoning_effort", "medium") ?: "medium") }
    var codexAccount by remember { mutableStateOf(prefs.getString("codex_account", "account1") ?: "account1") }
    var codexModel by remember { mutableStateOf(prefs.getString("codex_model", "") ?: "") }
    var codexAccounts by remember { mutableStateOf<List<CodexAccount>>(emptyList()) }
    var codexModels by remember { mutableStateOf<List<CodexModel>>(emptyList()) }
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
    var selectedSessionReasoning by remember { mutableStateOf<String?>(null) }
    var selectedSessionAccount by remember { mutableStateOf<String?>(null) }
    var selectedSessionModel by remember { mutableStateOf<String?>(null) }
    var lastRecordingFile by remember { mutableStateOf<File?>(null) }
    var canRetryTranscription by remember { mutableStateOf(false) }
    var isTranscribing by remember { mutableStateOf(false) }
    var isSubmittingSession by remember { mutableStateOf(false) }
    var reconnectSessionId by remember { mutableStateOf<String?>(null) }
    var reconnectAttempt by remember { mutableStateOf(0) }
    var reconnectTrigger by remember { mutableStateOf(0) }
    var authDialogAccount by remember { mutableStateOf<CodexAccount?>(null) }
    var authLoginSession by remember { mutableStateOf<CodexLoginSession?>(null) }
    var authPollTrigger by remember { mutableStateOf(0) }
    var pendingAuthAccount by remember { mutableStateOf<CodexAccount?>(null) }
    val hasCurrentSession = selectedSessionId != null || sessionId != null

    fun activeSessionId(): String? = selectedSessionId ?: sessionId

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
        selectedSessionReasoning = reasoningEffort
        selectedSessionAccount = codexAccount
        selectedSessionModel = codexModel
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
                "auth_required" -> {
                    val accountId = event.optString("account_id", selectedSessionAccount ?: codexAccount)
                    val data = event.optString("data")
                    if (data.isNotBlank()) terminal += data
                    status = "Login needed"
                    val account = codexAccounts.firstOrNull { it.accountId == accountId }
                        ?: CodexAccount(accountId, codexAccountLabel(accountId, codexAccounts), false, false)
                    authDialogAccount = account
                }
                "status" -> {
                    val nextStatus = event.optString("status", status)
                    status = nextStatus
                    when (nextStatus) {
                        "running" -> {
                            sessionRunning = true
                            resetReconnect()
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

    fun loadSession(id: String, expandTerminal: Boolean) {
        resetReconnect()
        selectedSessionId = id
        sessionId = id
        status = "Loading $id"
        terminalExpanded = terminalExpanded || expandTerminal
        socket?.close(1000, "notification session")
        socket = null
        sessionRunning = false
        fun loadInfoAndMaybeReconnect() {
            fetchSessionInfo(client, gatewayUrl, token, id) { infoResult ->
                infoResult.onSuccess { info ->
                    selectedSessionTitle = info.displayTitle.ifBlank { info.title }
                    selectedSessionReasoning = info.reasoningEffort
                    selectedSessionAccount = info.codexAccount
                    selectedSessionModel = info.codexModel
                    if (info.status == "running") {
                        attachSession(id, selectedSessionTitle ?: info.title)
                    } else {
                        status = "Selected $id"
                    }
                }.onFailure { error ->
                    terminal += "\n[session info error] ${error.message}\n"
                }
            }
        }
        fetchSessionLog(client, gatewayUrl, token, id) { result ->
            result.onSuccess { history ->
                terminal = history
                status = "Selected $id"
                loadInfoAndMaybeReconnect()
            }.onFailure { error ->
                terminal += "\n[history error] ${error.message}\n"
                status = "History load failed"
                loadInfoAndMaybeReconnect()
            }
        }
    }

    fun loadSessionFromNotification(id: String) {
        loadSession(id, expandTerminal = true)
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
        socket?.close(1000, "new conversation")
        socket = null
        sessionId = null
        selectedSessionId = null
        selectedSessionTitle = null
        selectedSessionReasoning = null
        selectedSessionAccount = null
        selectedSessionModel = null
        sessionRunning = false
        transcript = ""
        replyText = ""
        terminal = ""
        terminalExpanded = false
        lastRecordingFile = null
        canRetryTranscription = false
        status = "Ready"
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
        resumeSession(client, gatewayUrl, token, target, text, reasoningEffort, codexAccount, codexModel) { result ->
            result.onSuccess { id ->
                terminal += "\n[resume] Continuing $target as $id\n"
                attachSession(id)
            }.onFailure {
                status = "Resume failed"
                terminal += "\n[resume error] ${it.message}\n"
            }
        }
    }

    fun transcribeRecording(file: File) {
        if (!file.exists()) {
            status = "Recording unavailable"
            terminal += "\n[transcription error] The last recording is no longer available.\n"
            lastRecordingFile = null
            canRetryTranscription = false
            return
        }
        isTranscribing = true
        canRetryTranscription = false
        status = "Transcribing"
        transcribe(client, gatewayUrl, token, file) { result ->
            isTranscribing = false
            result.onSuccess {
                if (hasCurrentSession) {
                    replyText = it
                } else {
                    transcript = it
                }
                lastRecordingFile = file
                canRetryTranscription = false
                status = "Transcript ready"
            }.onFailure {
                status = "Transcription failed"
                lastRecordingFile = file
                canRetryTranscription = true
                terminal += "\n[transcription error] ${it.friendlyMessage()}\n"
            }
        }
    }

    fun finishRecording() {
        try {
            val file = recorderState.stop()
            lastRecordingFile = file
            status = "Transcribing"
            transcribeRecording(file)
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
        if (isSubmittingSession) return
        val target = selectedSessionId
        isSubmittingSession = true
        status = if (target == null) "Starting Codex" else "Resuming $target"
        terminalExpanded = true
        if (target == null) {
            terminal = ""
        }
        val callback: (Result<String>) -> Unit = { result ->
            isSubmittingSession = false
            result.onSuccess { id ->
                attachSession(id)
            }.onFailure {
                status = if (target == null) "Start failed" else "Resume failed"
                terminal += "\n[session error] ${it.message}\n"
            }
        }
        if (target == null) {
            startSession(client, gatewayUrl, token, transcript, reasoningEffort, codexAccount, codexModel, callback)
        } else {
            resumeSession(client, gatewayUrl, token, target, transcript, reasoningEffort, codexAccount, codexModel, callback)
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
        refreshCodexAccounts()
        refreshCodexModels()
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
        status = "Reconnecting $target"
        fetchSessionInfo(client, gatewayUrl, token, target) { result ->
            result.onSuccess { info ->
                if (activeSessionId() == target && reconnectSessionId == target) {
                    if (info.status == "running") {
                        fetchSessionLog(client, gatewayUrl, token, target) { historyResult ->
                            historyResult.onSuccess { terminal = it }
                            attachSession(target, info.displayTitle.ifBlank { info.title })
                        }
                    } else {
                        reconnectSessionId = null
                        sessionRunning = false
                        status = "Selected $target"
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

    DisposableEffect(lifecycleOwner, selectedSessionId, sessionId, sessionRunning, status) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) {
                val target = activeSessionId()
                if (target != null && (!sessionRunning || status == "Disconnected" || status == "closed")) {
                    loadSession(target, expandTerminal = false)
                }
            }
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose {
            lifecycleOwner.lifecycle.removeObserver(observer)
        }
    }

    if (showSettings) {
        SettingsDialog(
            gatewayUrl = gatewayUrl,
            token = token,
            reasoningEffort = reasoningEffort,
            codexAccount = codexAccount,
            codexAccounts = codexAccounts,
            codexModel = codexModel,
            codexModels = codexModels,
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
            onRefreshAccounts = ::refreshCodexAccounts,
            onRefreshModels = ::refreshCodexModels,
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

    Box(modifier = Modifier.fillMaxSize()) {
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
                    if (!isTranscribing) {
                        if (recorderState.isRecording.value) finishRecording() else startRecording()
                    }
                }
            )

            RetryTranscribeButton(
                visible = canRetryTranscription && lastRecordingFile != null && !recorderState.isRecording.value,
                isTranscribing = isTranscribing,
                enabled = gatewayUrl.isNotBlank(),
                onRetry = { lastRecordingFile?.let(::transcribeRecording) }
            )

            Terminal(
                text = terminal,
                expanded = true,
                onExpand = { terminalExpanded = true },
                onCollapse = { terminalExpanded = false },
                onNewConversation = ::startNewConversation,
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

        selectedSessionTitle?.let {
            val effort = selectedSessionReasoning?.let { value -> " - ${reasoningLabel(value)}" }.orEmpty()
            val account = selectedSessionAccount?.let { value -> " - ${codexAccountLabel(value, codexAccounts)}" }.orEmpty()
            val model = selectedSessionModel?.let { value -> " - ${codexModelLabel(value, codexModels)}" }.orEmpty()
            Text("Selected: $it$effort$account$model", color = Muted, style = MaterialTheme.typography.bodySmall)
        }

        TalkButton(
            isRecording = recorderState.isRecording.value,
            targetLabel = if (hasCurrentSession) "Reply" else "Task",
            onClick = {
                if (!isTranscribing) {
                    if (recorderState.isRecording.value) finishRecording() else startRecording()
                }
            }
        )

        RetryTranscribeButton(
            visible = canRetryTranscription && lastRecordingFile != null && !recorderState.isRecording.value,
            isTranscribing = isTranscribing,
            enabled = gatewayUrl.isNotBlank(),
            onRetry = { lastRecordingFile?.let(::transcribeRecording) }
        )

        OutlinedTextField(
            value = transcript,
            onValueChange = { transcript = it },
            label = { Text("Transcript") },
            modifier = Modifier.fillMaxWidth(),
            minLines = 3,
            maxLines = 5
        )

        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            Button(
                onClick = ::runCodex,
                enabled = transcript.isNotBlank() && gatewayUrl.isNotBlank() && !isSubmittingSession,
                modifier = Modifier.weight(1f),
                colors = ButtonDefaults.buttonColors(containerColor = Primary)
            ) {
                Text(
                    when {
                        isSubmittingSession -> "Sending"
                        selectedSessionId == null -> "Run Codex"
                        else -> "Resume Codex"
                    }
                )
            }

            OutlinedButton(
                onClick = ::startNewConversation,
                modifier = Modifier.weight(1f)
            ) {
                Text("New Chat")
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

        if (showSessions) {
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .background(Color(0x66000000))
            ) {
                SessionDrawer(
                    sessions = sessions,
                    selectedSessionId = selectedSessionId,
                    onRefresh = ::refreshSessions,
                    onDismiss = { showSessions = false },
                    onSelect = { session ->
                        selectedSessionTitle = session.displayTitle.ifBlank { session.title }
                        selectedSessionReasoning = session.reasoningEffort.takeIf { value -> value.isNotBlank() }
                        selectedSessionAccount = session.codexAccount.takeIf { value -> value.isNotBlank() }
                        selectedSessionModel = session.codexModel.takeIf { value -> value.isNotBlank() }
                        loadSession(session.sessionId, expandTerminal = true)
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


fun codexAccountLabel(value: String, accounts: List<CodexAccount>): String {
    return accounts.firstOrNull { it.accountId == value }?.label ?: value
}


fun codexModelLabel(value: String, models: List<CodexModel>): String {
    return models.firstOrNull { it.modelId == value }?.label ?: value
}


@Composable
fun Header(status: String, onSessions: () -> Unit, onSettings: () -> Unit) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .heightIn(min = 44.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        IconButton(onClick = onSessions) {
            Icon(painterResource(R.drawable.ic_menu_24), contentDescription = "Sessions", tint = PrimaryDark)
        }
        Column(modifier = Modifier.weight(1f)) {
            Text("Home Agent", color = Ink, fontWeight = FontWeight.Bold)
            Text(status, color = Muted, style = MaterialTheme.typography.bodySmall)
        }
        IconButton(onClick = onSettings) {
            Icon(painterResource(R.drawable.ic_settings_24), contentDescription = "Settings", tint = PrimaryDark)
        }
    }
}


@Composable
fun SettingsDialog(
    gatewayUrl: String,
    token: String,
    reasoningEffort: String,
    codexAccount: String,
    codexAccounts: List<CodexAccount>,
    codexModel: String,
    codexModels: List<CodexModel>,
    onGatewayUrl: (String) -> Unit,
    onToken: (String) -> Unit,
    onReasoningEffort: (String) -> Unit,
    onCodexAccount: (String) -> Unit,
    onCodexModel: (String) -> Unit,
    onRefreshAccounts: () -> Unit,
    onRefreshModels: () -> Unit,
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
                ReasoningSelector(
                    selected = reasoningEffort,
                    sessionReasoning = null,
                    onSelected = onReasoningEffort
                )
                CodexAccountSelector(
                    selected = codexAccount,
                    accounts = codexAccounts,
                    onSelected = onCodexAccount,
                    onRefresh = onRefreshAccounts,
                    onSaveLabel = onSaveCodexAccountLabel,
                    onStartLogin = onStartCodexLogin
                )
                CodexModelSelector(
                    selected = codexModel,
                    models = codexModels,
                    onSelected = onCodexModel,
                    onRefresh = onRefreshModels
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
fun CodexModelSelector(
    selected: String,
    models: List<CodexModel>,
    onSelected: (String) -> Unit,
    onRefresh: () -> Unit
) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text("Codex model", color = Ink, fontWeight = FontWeight.Medium)
            TextButton(onClick = onRefresh) {
                Text("Refresh", color = Primary)
            }
        }
        val options = if (models.isEmpty()) {
            listOf(CodexModel(selected.ifBlank { "gpt-5.5" }, selected.ifBlank { "GPT-5.5" }, "", true, false, null))
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
    selectedSessionId: String?,
    onRefresh: () -> Unit,
    onDismiss: () -> Unit,
    onSelect: (AgentSession) -> Unit
) {
    val groups = remember(sessions) { groupedSessions(sessions) }
    var expandedGroups by remember(groups) { mutableStateOf<Set<String>>(emptySet()) }
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
            if (sessions.isEmpty()) {
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


fun formatSessionTime(startedAt: Double): String {
    if (startedAt <= 0.0) return ""
    return SimpleDateFormat("MMM d h:mm a", Locale.US).format(Date((startedAt * 1000).toLong()))
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
fun RetryTranscribeButton(
    visible: Boolean,
    isTranscribing: Boolean,
    enabled: Boolean,
    onRetry: () -> Unit
) {
    if (!visible) return
    OutlinedButton(
        onClick = onRetry,
        enabled = enabled && !isTranscribing,
        modifier = Modifier.fillMaxWidth()
    ) {
        Text(if (isTranscribing) "Transcribing" else "Retry Transcribe Last Recording")
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
    onNewConversation: (() -> Unit)? = null,
    modifier: Modifier = Modifier
) {
    val scrollState = rememberScrollState()
    val blocks = remember(text) { markdownBlocks(text.ifBlank { "Terminal output will appear here." }) }
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
                Row(horizontalArrangement = Arrangement.spacedBy(4.dp), verticalAlignment = Alignment.CenterVertically) {
                    if (expanded && onNewConversation != null) {
                        TextButton(onClick = onNewConversation) {
                            Text("New Chat", color = Color(0xFFB7E3D4))
                        }
                    }
                    TextButton(onClick = if (expanded) onCollapse else onExpand) {
                        Text(if (expanded) "Collapse" else "Expand", color = Color(0xFFB7E3D4))
                    }
                }
            }
            SelectionContainer(
                modifier = Modifier
                    .fillMaxWidth()
                    .weight(1f)
            ) {
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .verticalScroll(scrollState),
                    verticalArrangement = Arrangement.spacedBy(6.dp)
                ) {
                    blocks.forEach { block ->
                        MarkdownTerminalBlock(block)
                    }
                }
            }
        }
    }
}


data class MarkdownBlock(val type: String, val text: String, val level: Int = 0)


fun markdownBlocks(text: String): List<MarkdownBlock> {
    val blocks = mutableListOf<MarkdownBlock>()
    val paragraph = mutableListOf<String>()
    val code = mutableListOf<String>()
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

    text.lines().forEach { line ->
        val trimmed = line.trimEnd()
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
    flushParagraph()
    return blocks
}


@Composable
fun MarkdownTerminalBlock(block: MarkdownBlock) {
    when (block.type) {
        "heading" -> Text(
            text = block.text,
            color = Color(0xFFF4F2ED),
            fontWeight = FontWeight.Bold,
            style = if (block.level <= 2) MaterialTheme.typography.titleMedium else MaterialTheme.typography.bodyLarge
        )
        "bullet" -> Text(
            text = terminalInlineAnnotated("- ${block.text}", terminalLineStyle(block.text)),
            style = MaterialTheme.typography.bodyMedium
        )
        "code" -> Text(
            text = block.text.ifBlank { " " },
            modifier = Modifier
                .fillMaxWidth()
                .background(Color(0xFF17221F), RoundedCornerShape(6.dp))
                .padding(8.dp),
            color = Color(0xFFE6EFE9),
            fontFamily = FontFamily.Monospace,
            style = MaterialTheme.typography.bodySmall
        )
        "terminal" -> Text(
            text = terminalInlineAnnotated(block.text, terminalLineStyle(block.text)),
            fontFamily = FontFamily.Monospace,
            style = MaterialTheme.typography.bodySmall
        )
        else -> Text(
            text = terminalInlineAnnotated(block.text, SpanStyle(color = Color(0xFFF4F2ED))),
            style = MaterialTheme.typography.bodyMedium
        )
    }
}


fun terminalInlineAnnotated(text: String, baseStyle: SpanStyle) = buildAnnotatedString {
    pushStyle(baseStyle)
    val pattern = Regex("(`[^`]+`|\\*\\*[^*]+\\*\\*)")
    var index = 0
    pattern.findAll(text).forEach { match ->
        append(text.substring(index, match.range.first))
        val token = match.value
        if (token.startsWith("`")) {
            pushStyle(SpanStyle(color = Color(0xFFE6EFE9), background = Color(0xFF17221F), fontFamily = FontFamily.Monospace))
            append(token.trim('`'))
            pop()
        } else {
            pushStyle(SpanStyle(fontWeight = FontWeight.Bold))
            append(token.removePrefix("**").removeSuffix("**"))
            pop()
        }
        index = match.range.last + 1
    }
    append(text.substring(index))
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
    reasoningEffort: String,
    codexAccount: String,
    codexModel: String,
    callback: (Result<String>) -> Unit
) {
    val json = JSONObject(
        mapOf(
            "text" to transcript,
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
    reasoningEffort: String,
    codexAccount: String,
    codexModel: String,
    callback: (Result<String>) -> Unit
) {
    val json = JSONObject(
        mapOf(
            "text" to text,
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
        reasoningEffort = item.optString("reasoning_effort", "medium"),
        codexAccount = item.optString("codex_account", "account1"),
        codexModel = item.optString("codex_model", ""),
        resumeFrom = item.optString("resume_from").takeIf { it.isNotBlank() && it != "null" }
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


fun postSessionNotification(context: Context, sessionId: String, title: String, message: String) {
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
    val pendingIntent = PendingIntent.getActivity(
        context,
        sessionId.hashCode(),
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
    manager.notify(SESSION_NOTIFICATION_ID + sessionId.hashCode(), notification)
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
                try {
                    onEvent(JSONObject(text))
                } catch (error: JSONException) {
                    onEvent(
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
                onEvent(JSONObject(mapOf("type" to "output", "data" to "\n[websocket error] ${t.message}\n")))
                onEvent(JSONObject(mapOf("type" to "status", "status" to "Disconnected")))
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
