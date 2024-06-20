if ! test -f /var/run/qubes-service/gpu-accel; then
  export GSK_RENDERER="cairo" GDK_DEBUG="gl-disable vulkan-disable" \
         LIBGL_ALWAYS_SOFTWARE=1
fi
