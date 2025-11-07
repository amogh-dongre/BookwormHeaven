document.addEventListener('DOMContentLoaded', () => {
    const hourHand = document.querySelector('.hour-hand');
    const minuteHand = document.querySelector('.minute-hand');
    const secondHand = document.querySelector('.second-hand');

    function updateClock() {
        const now = new Date();
        const seconds = now.getSeconds();
        const minutes = now.getMinutes();
        const hours = now.getHours();

        // Calculate degrees for each hand
        // Second hand moves 360 degrees in 60 seconds (6 degrees per second)
        const secondDegrees = (seconds / 60) * 360;
        // Minute hand moves 360 degrees in 60 minutes (6 degrees per minute)
        // Add seconds contribution (each second is 1/60th of a minute)
        const minuteDegrees = ((minutes + seconds / 60) / 60) * 360;
        // Hour hand moves 360 degrees in 12 hours (30 degrees per hour)
        // Add minutes contribution (each minute is 1/60th of an hour)
        const hourDegrees = ((hours % 12 + minutes / 60) / 12) * 360;

        if (secondHand) secondHand.style.transform = `rotate(${secondDegrees}deg)`;
        if (minuteHand) minuteHand.style.transform = `rotate(${minuteDegrees}deg)`;
        if (hourHand) hourHand.style.transform = `rotate(${hourDegrees}deg)`;
    }

    // Update clock every second
    if (hourHand && minuteHand && secondHand) {
        setInterval(updateClock, 1000);
        updateClock(); // Initial call to set the clock immediately
    }
});