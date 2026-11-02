"""One additional WebRTC viewer sharing the existing application framebuffer."""
def configure_spectator(settings=None):
    if settings is None:
        import carb.settings
        settings = carb.settings.get_settings()
    base='/exts/omni.kit.livestream.app'
    settings.set(base+'/primaryStream/allowDynamicResize',False)
    for index in range(3):
        for key,value in {'signalPort':49200+index*10,'streamPort':48000+index*2,'targetFps':30,'allowDynamicResize':False}.items():
            settings.set(base+f'/spectatorStream/{index}/'+key,value)
        settings.set(base+f'/spectatorStream/{index}/streamType','webrtc')
