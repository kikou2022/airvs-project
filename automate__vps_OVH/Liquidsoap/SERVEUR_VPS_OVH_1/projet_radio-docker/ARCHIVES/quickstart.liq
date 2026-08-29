#!/usr/local/bin/liquidsoap

#settings
settings.log.file := true
settings.log.file.path := "./log/liquidsoap.log"
settings.log.stdout := true
settings.log.level := 5
settings.decoder.priorities.ogg := 20
settings.decoder.file_extensions.ogg := ["ogv", "oga", "ogx", "ogg", "opus"]
runtime.gc.set(runtime.gc.get().{
  space_overhead = 20,
  allocation_policy = 2
})

#building sources
on_fail =
  single(
    "/Music/01 Before Meteor/01 - Prelude - Remembrance.oga"
  )
songRequests = request.queue(prefetch=3)
source = fallback(track_sensitive=false, [songRequests, on_fail])

source = mksafe(source)

#send track update method
def send_mq_event(payload) =
  def handle_http_error(error) =
    log.important(
      "Unable to send mq event. #{error.message}",
      label="http"
    )
  end

  def handle_http_response(result) =
    if
      result.status_code != 200
    then
      log.important(
        "Unable to send mq event. #{result.status_code} #{
          result.status_message
        } #{string.quote(result)}",
        label="http"
      )
    end
  end

  data = json.stringify(payload, compact=true)

  response =
    try
      http.post(
        "http://192.168.1.3:3040/api/liquidsoap",
        data=data,
        headers=[("Content-Type", "application/json")]
      )
    catch error do
      handle_http_error(error)
      null()
    end
  ignore(null.map(handle_http_response, response))
  ()
end

#process playlist from post
def addToQueue(req, _) =
  args = req.body()
  sep = ","
  let json.parse (tr : {tracks: [{uri: string, zoneId: string, trackId: string}], purge:bool}) = args
    log.important("#{tr.purge}")
  list.iter(fun (item) -> log.important("#{item}"), tr.tracks)
  list.iter(fun (item) -> songRequests.push.uri("annotate:comment=#{string.quote(string.concat([item.zoneId,sep,item.trackId]))}:#{item.uri}"), tr.tracks)
end

def clearQueue(_,res) =
  songRequests.set_queue([])
  res.content_type("application/json")
  res.data(json.stringify(songRequests.length(), compact=true))
end

def getQueue(_,res) =
  q = songRequests.queue()
  list.iter(fun(song)->log.important("#{request.metadata(song)}"),q)
end

#register playList endpoint
harbor.http.register(port=8000, method="get", "/queue",getQueue)
harbor.http.register(port=8000, method="post", "/queue", addToQueue)
harbor.http.register(port=8000, method="delete", "/queue", clearQueue)

#getting metadata from new track and sending to send_mq_event
def get_send_data(m) =
  def null_list(key, _list) =
    list.assoc.mem(key, _list) ? list.assoc(key, _list) : null()
  end

  np =
    {
      album=null_list("album", m),
      title=null_list("title", m),
      track=null_list("tracknumber",m),
      filename=null_list("filename", m),
      comment=null_list("comment",m)
    }
  log.important(
    "TRACK CHANGE:#{np}"
  )
  send_mq_event(np)
end

#bind get_send_data to source track change
source.on_metadata(get_send_data)

# Output the stream to icecast
output.icecast(
  %ogg(%flac),
  id="icecast",
  mount="vibes.ogg",
  host="icecast",
  port=8045,
  password="hackme",
  description=
    "no think vibe only",
  url="https://eorzeavibes.com",
  source
)
