package com.homeagent.phone

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.os.PowerManager

private const val RECORDING_CHANNEL_ID = "home_agent_dictation"
private const val RECORDING_NOTIFICATION_ID = 4201

// Keeps a dictation alive while the phone is locked or another app is in front.
// Without a microphone-type foreground service Android mutes a background
// app's mic and is free to freeze or kill the process; a long monologue with no
// screen touches lost its audio that way on 2026-10-18. The recorder itself
// lives in RecorderState (process scope) so this service only holds the
// foreground status, the mic FGS type and a wake lock.
class RecordingService : Service() {
    private var wakeLock: PowerManager.WakeLock? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        ensureRecordingChannel(this)
        val open = Intent(this, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_SINGLE_TOP
        }
        val pending = PendingIntent.getActivity(
            this,
            RECORDING_NOTIFICATION_ID,
            open,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val notification = Notification.Builder(this, RECORDING_CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_mic_24)
            .setContentTitle("Home Agent is recording")
            .setContentText("Dictation in progress. Tap to return and finish.")
            .setContentIntent(pending)
            .setOngoing(true)
            .build()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(RECORDING_NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
        } else {
            startForeground(RECORDING_NOTIFICATION_ID, notification)
        }
        wakeLock = (getSystemService(Context.POWER_SERVICE) as PowerManager)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "homeagent:dictation")
            .apply { acquire(2 * 60 * 60 * 1000L) }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int = START_NOT_STICKY

    override fun onTaskRemoved(rootIntent: Intent?) {
        // App swiped away mid-dictation: close the file cleanly so what was said
        // so far is offered for transcription on the next launch.
        runCatching { RecorderState.stop(this) }
        stopSelf()
    }

    override fun onDestroy() {
        wakeLock?.let { if (it.isHeld) it.release() }
        wakeLock = null
        super.onDestroy()
    }
}


fun ensureRecordingChannel(context: Context) {
    val manager = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
    val channel = NotificationChannel(
        RECORDING_CHANNEL_ID,
        "Dictation in progress",
        NotificationManager.IMPORTANCE_LOW
    ).apply {
        description = "Shown while Home Agent is recording a dictated prompt."
    }
    manager.createNotificationChannel(channel)
}
