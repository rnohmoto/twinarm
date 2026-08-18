from lerobot.robots.koch_follower import KochFollower, KochFollowerConfig

config = KochFollowerConfig(
    port="/dev/tty.usbmodem5B141156401",
    id="my_awesome_follower_arm",
)
follower = KochFollower(config)
follower.setup_motors()

