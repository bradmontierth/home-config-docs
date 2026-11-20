package com.homeagent.phone

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.widget.Toast
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.LinkAnnotation
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextLinkStyles
import androidx.compose.ui.text.withLink
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp

// Host that replaces localhost/127.0.0.1 in agent links: those point at the
// phone itself otherwise. Provided from the gateway URL's host.
val LocalLinkHost = compositionLocalOf<String?> { null }

private val LinkColor = Color(0xFF8CC8FF)

// http(s) URLs, plus bare host:port[/path] for LAN IPs, localhost, and
// tailnet (*.ts.net) names. A bare IP needs a port so plain address mentions
// ("the Pi at 192.168.10.92") stay text.
private val UrlPattern = Regex(
    """(?:https?://[^\s<>"'`\]]+)""" +
        """|(?<![\w./-])(?:(?:localhost|\d{1,3}(?:\.\d{1,3}){3}):\d{2,5}|[a-z0-9-]+(?:\.[a-z0-9-]+)*\.ts\.net(?::\d{2,5})?)(?:/[^\s<>"'`\]]*)?""",
    RegexOption.IGNORE_CASE
)

private const val TrailingJunk = ".,;:!?'\"*_>"

// Drop trailing punctuation that ends a sentence rather than the URL. A
// closing paren stays only when the URL opened one.
fun trimUrl(raw: String): String {
    var url = raw
    while (url.isNotEmpty()) {
        val last = url.last()
        url = when {
            last in TrailingJunk -> url.dropLast(1)
            last == ')' && url.count { it == '(' } < url.count { it == ')' } -> url.dropLast(1)
            else -> return url
        }
    }
    return url
}

// Full URL to open: adds http:// to bare hosts and points loopback at the
// gateway host.
fun openableUrl(found: String, linkHost: String?): String {
    val url = if (found.startsWith("http://", true) || found.startsWith("https://", true)) found else "http://$found"
    if (linkHost.isNullOrBlank()) return url
    return url.replaceFirst(Regex("""^(https?://)(?:localhost|127\.0\.0\.1)(?=[:/]|$)""", RegexOption.IGNORE_CASE), "$1$linkHost")
}

data class FoundLink(val start: Int, val end: Int, val url: String)

fun findLinks(text: String, linkHost: String?): List<FoundLink> =
    UrlPattern.findAll(text).mapNotNull { match ->
        val trimmed = trimUrl(match.value)
        if (trimmed.isEmpty() || !trimmed.any { it.isLetterOrDigit() }) null
        else FoundLink(match.range.first, match.range.first + trimmed.length, openableUrl(trimmed, linkHost))
    }.toList()

fun linkAnnotation(url: String) = LinkAnnotation.Url(
    url,
    TextLinkStyles(style = SpanStyle(color = LinkColor, textDecoration = TextDecoration.Underline))
)

// Appends `text`, turning every URL in it into a tappable link. Style pushed
// by the caller still applies.
fun AnnotatedString.Builder.appendLinkified(text: String, linkHost: String?) {
    var index = 0
    findLinks(text, linkHost).forEach { link ->
        append(text.substring(index, link.start))
        withLink(linkAnnotation(link.url)) { append(text.substring(link.start, link.end)) }
        index = link.end
    }
    append(text.substring(index))
}

fun linkifiedString(text: String, linkHost: String?): AnnotatedString =
    AnnotatedString.Builder().apply { appendLinkified(text, linkHost) }.toAnnotatedString()

private val MarkdownLink = Regex("""\[([^\]\n]+)]\(([^)\s]+)\)""")
private val UserEcho = Regex("""^> """, RegexOption.MULTILINE)

// Unique links in the newest reply: everything after the last echoed user
// message, so the bar follows the conversation.
fun latestReplyLinks(terminal: String, linkHost: String?): List<String> {
    val start = UserEcho.findAll(terminal).lastOrNull()?.range?.first ?: 0
    var tail = terminal.substring(start)
    // Skip the echoed message itself (a pasted prompt can contain links).
    tail = tail.substringAfter("\n[", tail)
    val urls = mutableListOf<String>()
    MarkdownLink.findAll(tail).forEach { urls.add(openableUrl(trimUrl(it.groupValues[2]), linkHost)) }
    findLinks(MarkdownLink.replace(tail, " "), linkHost).forEach { urls.add(it.url) }
    return urls.distinct()
}

fun openLink(context: Context, url: String) {
    try {
        context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    } catch (e: Exception) {
        Toast.makeText(context, "Can't open $url", Toast.LENGTH_SHORT).show()
    }
}

fun copyLink(context: Context, url: String) {
    val clipboard = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
    clipboard.setPrimaryClip(ClipData.newPlainText("link", url))
    Toast.makeText(context, "Copied", Toast.LENGTH_SHORT).show()
}

fun linkChipLabel(url: String): String =
    url.replaceFirst(Regex("^https?://", RegexOption.IGNORE_CASE), "").trimEnd('/')

@OptIn(ExperimentalFoundationApi::class)
@Composable
fun LinkBar(urls: List<String>, modifier: Modifier = Modifier) {
    if (urls.isEmpty()) return
    val context = LocalContext.current
    Row(
        modifier = modifier
            .fillMaxWidth()
            .horizontalScroll(rememberScrollState()),
        horizontalArrangement = Arrangement.spacedBy(6.dp)
    ) {
        urls.forEach { url ->
            Text(
                text = "↗ " + linkChipLabel(url),
                color = LinkColor,
                style = MaterialTheme.typography.bodySmall,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier
                    .widthIn(max = 260.dp)
                    .background(Color(0xFF22312D), RoundedCornerShape(14.dp))
                    .combinedClickable(
                        onClick = { openLink(context, url) },
                        onLongClick = { copyLink(context, url) }
                    )
                    .padding(horizontal = 10.dp, vertical = 6.dp)
            )
        }
    }
}
