// SparkCube vertex shader (uniform-register mode).
// u_mvp holds the four ROWS of a row-major model-view-projection matrix,
// so each clip coordinate is one dot product.
attribute vec3 a_position;
attribute vec3 a_color;

uniform vec4 u_mvp[4];

varying vec3 v_color;

void main()
{
    vec4 p = vec4(a_position, 1.0);
    gl_Position = vec4(dot(u_mvp[0], p),
                       dot(u_mvp[1], p),
                       dot(u_mvp[2], p),
                       dot(u_mvp[3], p));
    v_color = a_color;
}
