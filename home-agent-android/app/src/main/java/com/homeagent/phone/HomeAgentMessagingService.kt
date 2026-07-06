package com.homeagent.phone

import android.content.Context
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import java.util.concurrent.TimeUnit
import okhttp3.OkHttpClient


class HomeAgentMessagingService : FirebaseMessagingService() {
    override fun onNewToken(token: String) {
        super.onNewToken(token)
        val prefs = getSharedPreferences("home-agent", Context.MODE_PRIVATE)
        prefs.edit().putString("fcm_token", token).apply()
        val gatewayUrl = prefs.getString("gateway_url", "") ?: ""
        val gatewayToken = prefs.getString("token", "") ?: ""
        if (gatewayUrl.isBlank()) return

        registerDeviceToken(
            client = pushClient(),
            gatewayUrl = gatewayUrl,
            token = gatewayToken,
            fcmToken = token
        )
    }

    override fun onMessageReceived(message: RemoteMessage) {
        super.onMessageReceived(message)
        val data = message.data
        val sessionId = data["session_id"] ?: return
        val eventType = data["event_type"].orEmpty()
        val title = data["notification_title"] ?: message.notification?.title ?: when (eventType) {
            "approval_needed" -> "Home Agent needs approval"
            "finished" -> "Home Agent finished"
            "failed" -> "Home Agent failed"
            else -> "Home Agent"
        }
        val body = data["notification_body"] ?: message.notification?.body ?: when (eventType) {
            "approval_needed" -> "Tap to reopen and reconnect this session."
            "finished" -> "Tap to review the latest response."
            "failed" -> "Tap to review the failed session."
            else -> "Tap to reopen the session."
        }
        postSessionNotification(applicationContext, sessionId, title, body)
    }

    private fun pushClient(): OkHttpClient {
        return OkHttpClient.Builder()
            .connectTimeout(20, TimeUnit.SECONDS)
            .writeTimeout(30, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .callTimeout(60, TimeUnit.SECONDS)
            .build()
    }
}
